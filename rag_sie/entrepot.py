"""Stockage de l'index : documents, sections, blocs, vecteurs, droits.

Deux moteurs, même interface :
- EntrepotPostgres : PostgreSQL + pgvector, pour le serveur SIE (index HNSW, recherche plein
  texte française avec racinisation et sans accents). Tables préfixées « rag_ ».
- EntrepotSQLite   : un seul fichier, sans serveur, pour un poste de travail, la démonstration
  et les tests (recherche plein texte FTS5, vecteurs comparés en mémoire). Convient à quelques
  dizaines de milliers de blocs, pas aux 400 Go complets.

Les droits sont filtrés dans la requête elle-même : un passage non autorisé n'est jamais lu.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from .decoupage import Bloc, Section
from .droits import Utilisateur, cles_acces
from .texte import MOTS_VIDES, jetons, normaliser

TYPES_DOC = ("memoire", "dce", "prix", "attestation", "reference", "autre")


@dataclass
class DocumentRAG:
    chemin: str                     # relatif à la racine des archives, séparateur « / »
    sha256: str
    taille: int
    mtime: int
    comptes: list[str] = field(default_factory=list)
    affaire: str = ""
    annee: int | None = None
    type_doc: str = "autre"
    activites: list[str] = field(default_factory=list)
    type_ouvrage: str | None = None
    valide: bool | None = None
    resultat: str | None = None
    titre: str = ""
    envoyable: bool = True
    ocr: bool = False
    exclu: str | None = None        # raison si le fichier est volontairement ignoré
    remarque: str | None = None
    id: int | None = None


@dataclass
class Filtre:
    utilisateur: Utilisateur | None = None   # None : aucun filtre de droits (usage interne uniquement)
    types: list[str] | None = None
    valides_seulement: bool = True           # écarte les mémoires reconnus comme brouillons
    annee_min: int | None = None
    affaires: list[str] | None = None
    exclure_affaires: list[str] | None = None
    activites: list[str] | None = None
    type_ouvrage: str | None = None


@dataclass
class BlocTrouve:
    id: int
    document_id: int
    section_id: str
    ordre: int
    page: int
    chemin_titres: str
    texte: str
    fichier: str
    affaire: str
    annee: int | None
    type_doc: str
    activites: list[str]
    type_ouvrage: str | None
    valide: bool | None
    envoyable: bool
    titre: str
    score: float = 0.0
    scores: dict = field(default_factory=dict)

    def dict(self) -> dict:
        return asdict(self)


def mots_requete(requete: str, limite: int = 32) -> list[str]:
    """Mots de la requête pour la recherche plein texte (sans mots vides, sans doublons)."""
    vus, out = set(), []
    for m in normaliser(requete).replace("'", " ").split():
        m = "".join(c for c in m if c.isalnum())
        if len(m) >= 2 and m not in MOTS_VIDES and m not in vus:
            vus.add(m)
            out.append(m)
    return out[:limite]


class ErreurEncodeur(RuntimeError):
    pass


# =====================================================================================
# SQLite
# =====================================================================================
_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS rag_meta (cle TEXT PRIMARY KEY, valeur TEXT);
CREATE TABLE IF NOT EXISTS rag_documents (
  id INTEGER PRIMARY KEY, chemin TEXT UNIQUE NOT NULL, sha256 TEXT, taille INTEGER, mtime INTEGER,
  affaire TEXT, annee INTEGER, type_doc TEXT, activites TEXT, type_ouvrage TEXT, valide INTEGER,
  resultat TEXT, titre TEXT, envoyable INTEGER, ocr INTEGER, exclu TEXT, remarque TEXT,
  comptes TEXT, cles TEXT, indexe_le TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS rag_documents_sha ON rag_documents(sha256);
CREATE TABLE IF NOT EXISTS rag_sections (
  document_id INTEGER REFERENCES rag_documents(id) ON DELETE CASCADE, section_id TEXT,
  chemin TEXT, page INTEGER, texte TEXT, PRIMARY KEY (document_id, section_id));
CREATE TABLE IF NOT EXISTS rag_blocs (
  id INTEGER PRIMARY KEY, document_id INTEGER REFERENCES rag_documents(id) ON DELETE CASCADE,
  section_id TEXT, ordre INTEGER, page INTEGER, chemin TEXT, texte TEXT, vecteur BLOB);
CREATE INDEX IF NOT EXISTS rag_blocs_doc ON rag_blocs(document_id);
CREATE VIRTUAL TABLE IF NOT EXISTS rag_blocs_fts USING fts5(texte, tokenize = 'unicode61 remove_diacritics 2');
"""

_COLONNES_BLOC = """b.id, b.document_id, b.section_id, b.ordre, b.page, b.chemin, b.texte, d.chemin, d.affaire, d.annee,
d.type_doc, d.activites, d.type_ouvrage, d.valide, d.envoyable, d.titre"""


class EntrepotSQLite:
    moteur = "sqlite"

    def __init__(self, chemin: str | Path, encodeur_nom: str, dimension: int):
        Path(chemin).parent.mkdir(parents=True, exist_ok=True)
        self.cx = sqlite3.connect(str(chemin), check_same_thread=False)
        self.cx.execute("PRAGMA foreign_keys = ON")
        self.cx.execute("PRAGMA journal_mode = WAL")
        self.cx.executescript(_SCHEMA_SQLITE)
        self._verrou = threading.RLock()
        self._matrice: tuple[np.ndarray, np.ndarray] | None = None
        self.encodeur_nom, self.dimension = encodeur_nom, dimension
        _verifier_meta(self._meta_lire(), encodeur_nom, dimension, self._meta_ecrire)

    def _q(self, sql: str, args=()) -> list[tuple]:
        with self._verrou:
            return self.cx.execute(sql, args).fetchall()

    # --- méta ---
    def _meta_lire(self) -> dict:
        return dict(self._q("SELECT cle, valeur FROM rag_meta"))

    def _meta_ecrire(self, d: dict) -> None:
        with self.cx:
            self.cx.executemany("INSERT OR REPLACE INTO rag_meta VALUES (?, ?)", list(d.items()))

    # --- écriture ---
    def fichiers(self) -> dict[str, DocumentRAG]:
        rows = self._q("SELECT id, chemin, sha256, taille, mtime, comptes, exclu, type_doc FROM rag_documents")
        return {r[1]: DocumentRAG(id=r[0], chemin=r[1], sha256=r[2], taille=r[3], mtime=r[4],
                                  comptes=json.loads(r[5] or "[]"), exclu=r[6], type_doc=r[7]) for r in rows}

    def _supprimer(self, chemin: str) -> None:  # appelé sous verrou, dans une transaction
        ids = [r[0] for r in self.cx.execute(
            "SELECT b.id FROM rag_blocs b JOIN rag_documents d ON d.id = b.document_id WHERE d.chemin = ?", (chemin,))]
        if ids:
            self.cx.executemany("DELETE FROM rag_blocs_fts WHERE rowid = ?", [(i,) for i in ids])
        self.cx.execute("DELETE FROM rag_documents WHERE chemin = ?", (chemin,))

    def enregistrer(self, doc: DocumentRAG, secs: list[Section] = (), blocs: list[Bloc] = (),
                    vecteurs: np.ndarray | None = None) -> int:
        with self._verrou, self.cx:
            self._supprimer(doc.chemin)
            cur = self.cx.execute(
                """INSERT INTO rag_documents (chemin, sha256, taille, mtime, affaire, annee, type_doc, activites, type_ouvrage,
                   valide, resultat, titre, envoyable, ocr, exclu, remarque, comptes, cles)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc.chemin, doc.sha256, doc.taille, doc.mtime, doc.affaire, doc.annee, doc.type_doc,
                 json.dumps(doc.activites), doc.type_ouvrage, None if doc.valide is None else int(doc.valide), doc.resultat,
                 doc.titre, int(doc.envoyable), int(doc.ocr), doc.exclu, doc.remarque, json.dumps(doc.comptes),
                 json.dumps(cles_acces(doc.comptes))))
            doc_id = cur.lastrowid
            self.cx.executemany("INSERT INTO rag_sections VALUES (?,?,?,?,?)",
                                [(doc_id, s.id, s.chemin, s.page, s.texte[:20000]) for s in secs])
            for i, b in enumerate(blocs):
                v = vecteurs[i].astype(np.float32).tobytes() if vecteurs is not None else None
                cur = self.cx.execute("INSERT INTO rag_blocs (document_id, section_id, ordre, page, chemin, texte, vecteur) VALUES (?,?,?,?,?,?,?)",
                                      (doc_id, b.section_id, b.ordre, b.page, b.chemin, b.texte, v))
                self.cx.execute("INSERT INTO rag_blocs_fts (rowid, texte) VALUES (?, ?)",
                                (cur.lastrowid, " ".join(jetons(b.texte_indexe))))
            self._matrice = None
            return doc_id

    def deplacer(self, ancien: str, doc: DocumentRAG) -> None:
        with self._verrou, self.cx:
            self.cx.execute("""UPDATE rag_documents SET chemin=?, mtime=?, affaire=?, annee=?, type_ouvrage=?, valide=?,
                               titre=?, comptes=?, cles=? WHERE chemin=?""",
                            (doc.chemin, doc.mtime, doc.affaire, doc.annee, doc.type_ouvrage,
                             None if doc.valide is None else int(doc.valide), doc.titre, json.dumps(doc.comptes),
                             json.dumps(cles_acces(doc.comptes)), ancien))

    def maj_comptes(self, chemin: str, comptes: list[str]) -> None:
        with self._verrou, self.cx:
            self.cx.execute("UPDATE rag_documents SET comptes=?, cles=? WHERE chemin=?",
                            (json.dumps(comptes), json.dumps(cles_acces(comptes)), chemin))

    def maj_date(self, chemin: str, mtime: int) -> None:
        with self._verrou, self.cx:
            self.cx.execute("UPDATE rag_documents SET mtime=? WHERE chemin=?", (mtime, chemin))

    def supprimer(self, chemin: str) -> None:
        with self._verrou, self.cx:
            self._supprimer(chemin)
            self._matrice = None

    # --- lecture ---
    def _where(self, f: Filtre) -> tuple[str, list]:
        cond, args = ["d.exclu IS NULL"], []
        if f.utilisateur is not None:
            cond.append("EXISTS (SELECT 1 FROM json_each(d.cles) WHERE value IN (SELECT value FROM json_each(?)))")
            args.append(json.dumps(sorted(f.utilisateur.identites() | {"*"})))
        if f.types:
            cond.append(f"d.type_doc IN ({','.join('?' * len(f.types))})")
            args += list(f.types)
        if f.valides_seulement:
            cond.append("(d.type_doc != 'memoire' OR d.valide IS NULL OR d.valide = 1)")
        if f.annee_min:
            cond.append("d.annee >= ?")
            args.append(f.annee_min)
        if f.affaires:
            cond.append(f"d.affaire IN ({','.join('?' * len(f.affaires))})")
            args += list(f.affaires)
        if f.exclure_affaires:
            cond.append(f"d.affaire NOT IN ({','.join('?' * len(f.exclure_affaires))})")
            args += list(f.exclure_affaires)
        if f.activites:
            cond.append(f"EXISTS (SELECT 1 FROM json_each(d.activites) WHERE value IN ({','.join('?' * len(f.activites))}))")
            args += list(f.activites)
        if f.type_ouvrage:
            cond.append("d.type_ouvrage = ?")
            args.append(f.type_ouvrage)
        return " AND ".join(cond), args

    def chercher_texte(self, requete: str, filtre: Filtre, k: int = 40) -> list[tuple[int, float]]:
        termes = sorted(set(jetons(requete)))[:32]
        if not termes:
            return []
        where, args = self._where(filtre)
        sql = f"""SELECT b.id, -bm25(rag_blocs_fts) AS s FROM rag_blocs_fts
                  JOIN rag_blocs b ON b.id = rag_blocs_fts.rowid JOIN rag_documents d ON d.id = b.document_id
                  WHERE rag_blocs_fts MATCH ? AND {where} ORDER BY s DESC LIMIT ?"""
        q = " OR ".join(f"{t}*" for t in termes)
        return [(r[0], float(r[1])) for r in self._q(sql, [q, *args, k])]

    def chercher_vecteur(self, vecteur: np.ndarray, filtre: Filtre, k: int = 40) -> list[tuple[int, float]]:
        where, args = self._where(filtre)
        ids = np.array([r[0] for r in self._q(
            f"SELECT b.id FROM rag_blocs b JOIN rag_documents d ON d.id = b.document_id WHERE {where} AND b.vecteur IS NOT NULL ORDER BY b.id",
            args)], dtype=np.int64)
        if not len(ids):
            return []
        tous_ids, matrice = self._matrice_vecteurs()
        pos = np.searchsorted(tous_ids, ids)
        scores = matrice[pos] @ vecteur.astype(np.float32)
        ordre = np.argsort(-scores)[:k]
        return [(int(ids[i]), float(scores[i])) for i in ordre]

    def _matrice_vecteurs(self) -> tuple[np.ndarray, np.ndarray]:
        with self._verrou:
            if self._matrice is None:
                rows = self._q("SELECT id, vecteur FROM rag_blocs WHERE vecteur IS NOT NULL ORDER BY id")
                ids = np.array([r[0] for r in rows], dtype=np.int64)
                m = np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), self.dimension) \
                    if rows else np.zeros((0, self.dimension), dtype=np.float32)
                self._matrice = (ids, m)
            return self._matrice

    def blocs(self, ids: Iterable[int]) -> list[BlocTrouve]:
        ids = list(ids)
        if not ids:
            return []
        rows = self._q(f"SELECT {_COLONNES_BLOC} FROM rag_blocs b JOIN rag_documents d ON d.id = b.document_id "
                       f"WHERE b.id IN ({','.join('?' * len(ids))})", ids)
        par_id = {r[0]: _bloc_de_ligne(r, json.loads(r[11] or "[]")) for r in rows}
        return [par_id[i] for i in ids if i in par_id]

    def section(self, document_id: int, section_id: str) -> tuple[str, str] | None:
        r = self._q("SELECT chemin, texte FROM rag_sections WHERE document_id=? AND section_id=?", (document_id, section_id))
        return (r[0][0], r[0][1]) if r else None

    def document(self, document_id: int) -> dict | None:
        rows = self._q("""SELECT id, chemin, affaire, annee, type_doc, activites, type_ouvrage, valide, resultat, titre,
                          envoyable, ocr, remarque, comptes, indexe_le FROM rag_documents WHERE id=? AND exclu IS NULL""", (document_id,))
        if not rows:
            return None
        r = rows[0]
        secs = self._q("SELECT section_id, chemin, page FROM rag_sections WHERE document_id=? ORDER BY rowid", (document_id,))
        return {"id": r[0], "fichier": r[1], "affaire": r[2], "annee": r[3], "type_doc": r[4], "activites": json.loads(r[5] or "[]"),
                "type_ouvrage": r[6], "valide": None if r[7] is None else bool(r[7]), "resultat": r[8], "titre": r[9],
                "envoyable": bool(r[10]), "ocr": bool(r[11]), "remarque": r[12], "comptes": json.loads(r[13] or "[]"),
                "indexe_le": str(r[14]), "sections": [{"id": s[0], "titres": s[1], "page": s[2]} for s in secs]}

    def stats(self) -> dict:
        par_type = dict(self._q("SELECT type_doc, count(*) FROM rag_documents WHERE exclu IS NULL GROUP BY type_doc"))
        return {"moteur": self.moteur, "encodeur": self.encodeur_nom, "dimension": self.dimension,
                "documents": sum(par_type.values()), "par_type": par_type,
                "exclus": self._q("SELECT count(*) FROM rag_documents WHERE exclu IS NOT NULL")[0][0],
                "blocs": self._q("SELECT count(*) FROM rag_blocs")[0][0],
                "affaires": self._q("SELECT count(DISTINCT affaire) FROM rag_documents WHERE exclu IS NULL")[0][0]}

    def fermer(self) -> None:
        self.cx.close()


def _bloc_de_ligne(r, activites) -> BlocTrouve:
    return BlocTrouve(id=r[0], document_id=r[1], section_id=r[2], ordre=r[3], page=r[4], chemin_titres=r[5] or "", texte=r[6],
                      fichier=r[7], affaire=r[8], annee=r[9], type_doc=r[10], activites=activites, type_ouvrage=r[12],
                      valide=None if r[13] is None else bool(r[13]), envoyable=bool(r[14]), titre=r[15] or "")


def _verifier_meta(meta: dict, nom: str, dim: int, ecrire) -> None:
    if not meta.get("encodeur"):
        ecrire({"encodeur": nom, "dimension": str(dim), "version_schema": "1"})
    elif meta["encodeur"] != nom or int(meta.get("dimension", 0)) != dim:
        raise ErreurEncodeur(f"L'index a été construit avec l'encodeur « {meta['encodeur']} » ({meta.get('dimension')} dimensions) "
                             f"et non « {nom} » ({dim}). Utiliser le même encodeur, ou repartir d'une base vide (réindexation).")


# =====================================================================================
# PostgreSQL + pgvector
# =====================================================================================
def _schema_pg(dim: int) -> str:
    return f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS unaccent;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'sie_fr') THEN
    CREATE TEXT SEARCH CONFIGURATION sie_fr (COPY = french);
    ALTER TEXT SEARCH CONFIGURATION sie_fr ALTER MAPPING FOR hword, hword_part, word WITH unaccent, french_stem;
  END IF;
END $$;
CREATE TABLE IF NOT EXISTS rag_meta (cle text PRIMARY KEY, valeur text);
CREATE TABLE IF NOT EXISTS rag_documents (
  id bigserial PRIMARY KEY, chemin text UNIQUE NOT NULL, sha256 text, taille bigint, mtime bigint,
  affaire text, annee int, type_doc text, activites text[], type_ouvrage text, valide boolean,
  resultat text, titre text, envoyable boolean, ocr boolean, exclu text, remarque text,
  comptes text[], cles text[], indexe_le timestamptz DEFAULT now());
CREATE INDEX IF NOT EXISTS rag_documents_cles ON rag_documents USING gin (cles);
CREATE INDEX IF NOT EXISTS rag_documents_sha ON rag_documents (sha256);
CREATE INDEX IF NOT EXISTS rag_documents_affaire ON rag_documents (affaire);
CREATE TABLE IF NOT EXISTS rag_sections (
  document_id bigint REFERENCES rag_documents(id) ON DELETE CASCADE, section_id text,
  chemin text, page int, texte text, ordre serial, PRIMARY KEY (document_id, section_id));
CREATE TABLE IF NOT EXISTS rag_blocs (
  id bigserial PRIMARY KEY, document_id bigint REFERENCES rag_documents(id) ON DELETE CASCADE,
  section_id text, ordre int, page int, chemin text, texte text,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('sie_fr', coalesce(chemin, '') || ' ' || texte)) STORED,
  embedding vector({dim}));
CREATE INDEX IF NOT EXISTS rag_blocs_doc ON rag_blocs (document_id);
CREATE INDEX IF NOT EXISTS rag_blocs_tsv ON rag_blocs USING gin (tsv);
CREATE INDEX IF NOT EXISTS rag_blocs_vec ON rag_blocs USING hnsw (embedding vector_cosine_ops);
"""


def _vec(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v.tolist()) + "]"


class EntrepotPostgres:
    moteur = "postgres"

    def __init__(self, url: str, encodeur_nom: str, dimension: int):
        import psycopg

        self.url = url
        self.cx = psycopg.connect(url, autocommit=False)
        self._verrou = threading.RLock()
        self.encodeur_nom, self.dimension = encodeur_nom, dimension
        with self.cx.cursor() as c:
            c.execute("SELECT pg_advisory_xact_lock(4242)")  # deux services qui démarrent en même temps
            c.execute(_schema_pg(dimension))
        self.cx.commit()
        _verifier_meta(self._meta_lire(), encodeur_nom, dimension, self._meta_ecrire)

    def _meta_lire(self) -> dict:
        with self._verrou, self.cx.cursor() as c:
            c.execute("SELECT cle, valeur FROM rag_meta")
            r = dict(c.fetchall())
        self.cx.commit()
        return r

    def _meta_ecrire(self, d: dict) -> None:
        with self._verrou, self.cx.cursor() as c:
            c.executemany("INSERT INTO rag_meta VALUES (%s, %s) ON CONFLICT (cle) DO UPDATE SET valeur = EXCLUDED.valeur",
                          list(d.items()))
        self.cx.commit()

    def _verifier_connexion(self) -> None:
        """Reconnexion après un redémarrage de PostgreSQL ou une coupure réseau (appelé sous verrou)."""
        if self.cx.closed or self.cx.broken:
            import psycopg
            self.cx = psycopg.connect(self.url, autocommit=False)

    def _lire(self, sql: str, args=(), essai: int = 0) -> list[tuple]:
        import psycopg

        with self._verrou:
            self._verifier_connexion()
            try:
                with self.cx.cursor() as c:
                    c.execute(sql, args)
                    rows = c.fetchall()
                self.cx.commit()
                return rows
            except psycopg.OperationalError:
                if essai == 0 and self.cx.broken:  # connexion coupée : une nouvelle tentative
                    return self._lire(sql, args, essai=1)
                raise
            except Exception:
                self.cx.rollback()
                raise

    def fichiers(self) -> dict[str, DocumentRAG]:
        rows = self._lire("SELECT id, chemin, sha256, taille, mtime, comptes, exclu, type_doc FROM rag_documents")
        return {r[1]: DocumentRAG(id=r[0], chemin=r[1], sha256=r[2], taille=r[3], mtime=r[4], comptes=list(r[5] or []),
                                  exclu=r[6], type_doc=r[7]) for r in rows}

    def enregistrer(self, doc: DocumentRAG, secs: list[Section] = (), blocs: list[Bloc] = (),
                    vecteurs: np.ndarray | None = None) -> int:
        with self._verrou:
            self._verifier_connexion()
            try:
                with self.cx.cursor() as c:
                    c.execute("DELETE FROM rag_documents WHERE chemin = %s", (doc.chemin,))
                    c.execute("""INSERT INTO rag_documents (chemin, sha256, taille, mtime, affaire, annee, type_doc, activites,
                                 type_ouvrage, valide, resultat, titre, envoyable, ocr, exclu, remarque, comptes, cles)
                                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                              (doc.chemin, doc.sha256, doc.taille, doc.mtime, doc.affaire, doc.annee, doc.type_doc,
                               doc.activites, doc.type_ouvrage, doc.valide, doc.resultat, doc.titre, doc.envoyable,
                               doc.ocr, doc.exclu, doc.remarque, doc.comptes, cles_acces(doc.comptes)))
                    doc_id = c.fetchone()[0]
                    if secs:
                        c.executemany("INSERT INTO rag_sections (document_id, section_id, chemin, page, texte) VALUES (%s,%s,%s,%s,%s)",
                                      [(doc_id, s.id, s.chemin, s.page, s.texte[:20000]) for s in secs])
                    if blocs:
                        c.executemany("""INSERT INTO rag_blocs (document_id, section_id, ordre, page, chemin, texte, embedding)
                                         VALUES (%s,%s,%s,%s,%s,%s,%s::vector)""",
                                      [(doc_id, b.section_id, b.ordre, b.page, b.chemin, b.texte,
                                        _vec(vecteurs[i]) if vecteurs is not None else None) for i, b in enumerate(blocs)])
                self.cx.commit()
                return doc_id
            except Exception:
                self.cx.rollback()
                raise

    def _ecrire(self, sql: str, args) -> None:
        with self._verrou:
            self._verifier_connexion()
            try:
                with self.cx.cursor() as c:
                    c.execute(sql, args)
                self.cx.commit()
            except Exception:
                self.cx.rollback()
                raise

    def deplacer(self, ancien: str, doc: DocumentRAG) -> None:
        self._ecrire("""UPDATE rag_documents SET chemin=%s, mtime=%s, affaire=%s, annee=%s, type_ouvrage=%s, valide=%s, titre=%s,
                        comptes=%s, cles=%s WHERE chemin=%s""",
                     (doc.chemin, doc.mtime, doc.affaire, doc.annee, doc.type_ouvrage, doc.valide, doc.titre, doc.comptes,
                      cles_acces(doc.comptes), ancien))

    def maj_comptes(self, chemin: str, comptes: list[str]) -> None:
        self._ecrire("UPDATE rag_documents SET comptes=%s, cles=%s WHERE chemin=%s", (comptes, cles_acces(comptes), chemin))

    def maj_date(self, chemin: str, mtime: int) -> None:
        self._ecrire("UPDATE rag_documents SET mtime=%s WHERE chemin=%s", (mtime, chemin))

    def supprimer(self, chemin: str) -> None:
        self._ecrire("DELETE FROM rag_documents WHERE chemin=%s", (chemin,))

    def _where(self, f: Filtre) -> tuple[str, list]:
        cond, args = ["d.exclu IS NULL"], []
        if f.utilisateur is not None:
            cond.append("d.cles && %s::text[]")
            args.append(sorted(f.utilisateur.identites() | {"*"}))
        if f.types:
            cond.append("d.type_doc = ANY(%s)")
            args.append(list(f.types))
        if f.valides_seulement:
            cond.append("(d.type_doc <> 'memoire' OR d.valide IS NOT FALSE)")
        if f.annee_min:
            cond.append("d.annee >= %s")
            args.append(f.annee_min)
        if f.affaires:
            cond.append("d.affaire = ANY(%s)")
            args.append(list(f.affaires))
        if f.exclure_affaires:
            cond.append("NOT (d.affaire = ANY(%s))")
            args.append(list(f.exclure_affaires))
        if f.activites:
            cond.append("d.activites && %s::text[]")
            args.append(list(f.activites))
        if f.type_ouvrage:
            cond.append("d.type_ouvrage = %s")
            args.append(f.type_ouvrage)
        return " AND ".join(cond), args

    def chercher_texte(self, requete: str, filtre: Filtre, k: int = 40) -> list[tuple[int, float]]:
        mots = mots_requete(requete)
        if not mots:
            return []
        where, args = self._where(filtre)
        sql = f"""SELECT b.id, ts_rank_cd(b.tsv, q) AS s
                  FROM rag_blocs b JOIN rag_documents d ON d.id = b.document_id, to_tsquery('sie_fr', %s) q
                  WHERE b.tsv @@ q AND {where} ORDER BY s DESC LIMIT %s"""
        return [(r[0], float(r[1])) for r in self._lire(sql, [" | ".join(mots), *args, k])]

    def chercher_vecteur(self, vecteur: np.ndarray, filtre: Filtre, k: int = 40) -> list[tuple[int, float]]:
        where, args = self._where(filtre)
        sql = f"""SELECT b.id, 1 - (b.embedding <=> %s::vector) AS s
                  FROM rag_blocs b JOIN rag_documents d ON d.id = b.document_id
                  WHERE b.embedding IS NOT NULL AND {where} ORDER BY b.embedding <=> %s::vector LIMIT %s"""
        v = _vec(vecteur)
        with self._verrou:
            self._verifier_connexion()
            try:
                with self.cx.cursor() as c:
                    c.execute("SET LOCAL hnsw.ef_search = 200")
                    c.execute(sql, [v, *args, v, k])
                    rows = c.fetchall()
                    if len(rows) < k:
                        # Filtre très sélectif (droits, affaire) : l'index approché peut rendre trop peu
                        # de résultats ; on refait un parcours exact, rapide sur un sous-ensemble réduit.
                        c.execute("SET LOCAL enable_indexscan = off")
                        c.execute(sql, [v, *args, v, k])
                        rows = c.fetchall()
                self.cx.commit()
            except Exception:
                self.cx.rollback()
                raise
        return [(r[0], float(r[1])) for r in rows]

    def blocs(self, ids: Iterable[int]) -> list[BlocTrouve]:
        ids = list(ids)
        if not ids:
            return []
        rows = self._lire(f"SELECT {_COLONNES_BLOC} FROM rag_blocs b JOIN rag_documents d ON d.id = b.document_id "
                          f"WHERE b.id = ANY(%s)", (ids,))
        par_id = {r[0]: _bloc_de_ligne(r, list(r[11] or [])) for r in rows}
        return [par_id[i] for i in ids if i in par_id]

    def section(self, document_id: int, section_id: str) -> tuple[str, str] | None:
        rows = self._lire("SELECT chemin, texte FROM rag_sections WHERE document_id=%s AND section_id=%s", (document_id, section_id))
        return (rows[0][0], rows[0][1]) if rows else None

    def document(self, document_id: int) -> dict | None:
        rows = self._lire("""SELECT id, chemin, affaire, annee, type_doc, activites, type_ouvrage, valide, resultat, titre,
                             envoyable, ocr, remarque, comptes, indexe_le FROM rag_documents WHERE id=%s AND exclu IS NULL""",
                          (document_id,))
        if not rows:
            return None
        r = rows[0]
        secs = self._lire("SELECT section_id, chemin, page FROM rag_sections WHERE document_id=%s ORDER BY ordre", (document_id,))
        return {"id": r[0], "fichier": r[1], "affaire": r[2], "annee": r[3], "type_doc": r[4], "activites": list(r[5] or []),
                "type_ouvrage": r[6], "valide": r[7], "resultat": r[8], "titre": r[9], "envoyable": r[10], "ocr": r[11],
                "remarque": r[12], "comptes": list(r[13] or []), "indexe_le": str(r[14]),
                "sections": [{"id": s[0], "titres": s[1], "page": s[2]} for s in secs]}

    def stats(self) -> dict:
        par_type = dict(self._lire("SELECT type_doc, count(*) FROM rag_documents WHERE exclu IS NULL GROUP BY type_doc"))
        return {"moteur": self.moteur, "encodeur": self.encodeur_nom, "dimension": self.dimension,
                "documents": sum(par_type.values()), "par_type": par_type,
                "exclus": self._lire("SELECT count(*) FROM rag_documents WHERE exclu IS NOT NULL")[0][0],
                "blocs": self._lire("SELECT count(*) FROM rag_blocs")[0][0],
                "affaires": self._lire("SELECT count(DISTINCT affaire) FROM rag_documents WHERE exclu IS NULL")[0][0]}

    def fermer(self) -> None:
        self.cx.close()


def ouvrir(base: str, encodeur_nom: str, dimension: int):
    if base.startswith(("postgresql://", "postgres://")):
        return EntrepotPostgres(base, encodeur_nom, dimension)
    return EntrepotSQLite(base, encodeur_nom, dimension)
