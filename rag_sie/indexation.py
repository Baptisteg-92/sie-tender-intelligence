"""Synchronisation incrémentale du partage d'archives avec l'index.

Chaque nuit (tâche planifiée, 2 h par défaut) ou sur le bouton « Synchroniser maintenant » :

1. on liste le partage (lecture seule) ;
2. un fichier dont la taille et la date n'ont pas bougé n'est pas relu ;
3. un fichier modifié est relu et réindexé ; un fichier déplacé ou renommé (même empreinte SHA-256)
   est simplement mis à jour, sans recalcul ;
4. un fichier disparu du partage est retiré de l'index (avec un garde-fou si le partage semble
   inaccessible : on ne vide jamais l'index sur une coupure réseau) ;
5. les droits Windows sont relus, pour qu'un retrait d'accès soit pris en compte dès la nuit suivante.

Un verrou empêche deux synchronisations simultanées ; l'état (progression, dernier succès, nombre
de fichiers, erreurs) est écrit dans un fichier lu par l'interface. Chaque passage est journalisé.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import ConfigRAG
from .decoupage import blocs as decouper_blocs
from .decoupage import sections as decouper_sections
from .droits import LecteurDroits, normaliser_comptes
from .encodeurs import Encodeur
from .entrepot import DocumentRAG
from .exclusions import envoyable, raison_contenu, raison_exclusion
from .lecture import lire
from .metadonnees import metadonnees
from .texte import normaliser

IGNORES = {"thumbs.db", "desktop.ini", "affaire.json", "_droits.json"}
TAILLE_MAX_MO = 200  # au-delà : plans ou scans massifs, sans intérêt pour la rédaction
LOT_VECTEURS = 64
SUPPRESSIONS_MIN = 20  # garde-fou : au-delà de ce nombre ET de 30 % de l'index, on s'arrête


@dataclass
class CompteRendu:
    debut: str = ""
    fin: str = ""
    statut: str = "en_cours"   # en_cours | termine | partiel | echec
    vus: int = 0
    ajoutes: int = 0
    modifies: int = 0
    deplaces: int = 0
    supprimes: int = 0
    droits_modifies: int = 0
    inchanges: int = 0
    exclus: int = 0
    illisibles: int = 0
    blocs_ajoutes: int = 0
    erreurs: list[str] = field(default_factory=list)
    message: str = ""

    def resume(self) -> str:
        return (f"{self.vus} fichiers vus : {self.ajoutes} ajouté(s), {self.modifies} modifié(s), {self.deplaces} déplacé(s), "
                f"{self.supprimes} supprimé(s), {self.droits_modifies} changement(s) de droits, {self.exclus} écarté(s), "
                f"{self.illisibles} illisible(s), {len(self.erreurs)} erreur(s).")


# --- État partagé avec l'interface ------------------------------------------------------
class EtatSynchro:
    """Fichier synchro_etat.json : en cours ?, progression, dernier succès, derniers comptes rendus."""

    def __init__(self, dossier: Path):
        self.dossier = Path(dossier)
        self.dossier.mkdir(parents=True, exist_ok=True)
        self.fichier = self.dossier / "synchro_etat.json"
        self.verrou = self.dossier / "synchro.verrou"
        self.journal = self.dossier / "journal_synchro.jsonl"

    def lire(self) -> dict:
        try:
            etat = json.loads(self.fichier.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            etat = {}
        etat["en_cours"] = self.verrou_actif()
        return etat

    def ecrire(self, **valeurs) -> None:
        etat = self.lire()
        etat.update(valeurs)
        etat.pop("en_cours", None)
        tmp = self.fichier.with_suffix(".tmp")
        tmp.write_text(json.dumps(etat, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.fichier)

    # Verrou : fichier créé en exclusif, rafraîchi pendant le travail ; périmé après 30 min sans signe de vie.
    def verrou_actif(self) -> bool:
        try:
            return time.time() - self.verrou.stat().st_mtime < 1800
        except FileNotFoundError:
            return False

    def prendre(self) -> bool:
        if self.verrou.exists() and not self.verrou_actif():
            self.verrou.unlink(missing_ok=True)
        try:
            fd = os.open(self.verrou, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True

    def battre(self) -> None:
        try:
            os.utime(self.verrou)
        except FileNotFoundError:
            pass

    def rendre(self) -> None:
        self.verrou.unlink(missing_ok=True)

    def journaliser(self, cr: CompteRendu) -> None:
        with self.journal.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(cr), ensure_ascii=False) + "\n")


def empreinte(chemin: Path) -> str:
    h = hashlib.sha256()
    with chemin.open("rb") as f:
        for morceau in iter(lambda: f.read(1 << 20), b""):
            h.update(morceau)
    return h.hexdigest()


def _priorite(rel: str) -> tuple:
    """Mémoires d'abord, puis le reste : si la nuit ne suffit pas, l'essentiel est déjà indexé."""
    n = rel.lower()
    return (0 if "memoire" in n or "mémoire" in n else 1 if "reference" in n or "référence" in n else 2, n)


class Indexeur:
    def __init__(self, entrepot, encodeur: Encodeur, droits: LecteurDroits, config: ConfigRAG):
        self.entrepot, self.encodeur, self.droits, self.config = entrepot, encodeur, droits, config
        self.etat = EtatSynchro(config.donnees)

    # --- liste du partage ---
    def lister(self, racine: Path, erreurs: list[str]) -> dict[str, Path]:
        out: dict[str, Path] = {}
        exts = {e.lower() for e in self.config.extensions}

        def sur_erreur(e: OSError):
            erreurs.append(f"lecture du dossier impossible : {getattr(e, 'filename', '')} ({e.strerror})")

        for dossier, sous, fichiers in os.walk(racine, onerror=sur_erreur, followlinks=False):
            sous[:] = [d for d in sous if not d.startswith((".", "~", "$"))]
            for nom in fichiers:
                if nom.lower() in IGNORES or nom.startswith(("~$", ".", ".~lock")):
                    continue
                if Path(nom).suffix.lower() not in exts:
                    continue
                p = Path(dossier) / nom
                out[p.relative_to(racine).as_posix()] = p
        return out

    # --- un document ---
    def _document(self, racine: Path, rel: str, p: Path, st: os.stat_result, sha: str) -> tuple[DocumentRAG, list, list]:
        doc = DocumentRAG(chemin=rel, sha256=sha, taille=st.st_size, mtime=int(st.st_mtime), affaire=rel.split("/")[0])
        raison = raison_exclusion(rel, self.config.exclusions)
        if raison:
            doc.exclu = raison
            return doc, [], []
        if st.st_size > TAILLE_MAX_MO * 1024 * 1024:
            doc.remarque = f"fichier de plus de {TAILLE_MAX_MO} Mo non lu"
            return doc, [], []
        doc.comptes = self.droits.comptes(p)
        lu = lire(p)
        doc.ocr, doc.remarque = lu.ocr, lu.remarque
        texte = "\n".join(t for _, t in lu.pages)
        raison = raison_contenu(texte)
        if raison:
            doc.exclu, doc.comptes = raison, []
            return doc, [], []
        m = metadonnees(racine, p, texte, self.config.motif_valides)
        doc.affaire, doc.annee, doc.type_doc, doc.activites = m.affaire, m.annee, m.type_doc, m.activites
        doc.type_ouvrage, doc.valide, doc.resultat, doc.titre = m.type_ouvrage, m.valide, m.resultat, m.titre
        doc.envoyable = envoyable(rel, m.type_doc)
        if not lu.lisible:
            doc.remarque = doc.remarque or "aucun texte extrait"
            return doc, [], []
        secs = decouper_sections(lu.pages)
        return doc, secs, decouper_blocs(secs)

    def _vecteurs(self, bl):
        import numpy as np

        if not bl:
            return None
        parts = [self.encodeur.encoder([b.texte_indexe for b in bl[i:i + LOT_VECTEURS]]) for i in range(0, len(bl), LOT_VECTEURS)]
        return np.vstack(parts)

    # --- passage complet ---
    def synchroniser(self, racine: str | Path | None = None, forcer: bool = False, rafraichir_droits: bool = True,
                     duree_max_min: float | None = None, autoriser_suppressions_massives: bool = False,
                     suivi: Callable[[int, int, str], None] | None = None) -> CompteRendu:
        racine = Path(racine or self.config.archives)
        cr = CompteRendu(debut=datetime.now().isoformat(timespec="seconds"))
        if not self.etat.prendre():
            cr.statut, cr.message = "echec", "Une synchronisation est déjà en cours."
            return cr
        t0 = time.time()
        getattr(self.droits, "vider_cache", lambda: None)()
        try:
            if not str(racine) or not racine.is_dir():
                raise FileNotFoundError(f"Dossier des archives introuvable ou inaccessible : {racine}")
            self.etat.ecrire(debut=cr.debut, progression=[0, 0], fichier="", statut="en_cours")
            presents = self.lister(racine, cr.erreurs)
            connus = self.entrepot.fichiers()
            cr.vus = len(presents)
            disparus = {c for c in connus if c not in presents}
            if connus and not presents:
                raise RuntimeError("Le partage semble vide ou inaccessible : index conservé tel quel, aucune suppression.")
            if (len(disparus) > SUPPRESSIONS_MIN and len(disparus) > 0.3 * len(connus)) and not autoriser_suppressions_massives:
                raise RuntimeError(f"{len(disparus)} fichiers indexés ont disparu du partage (plus de 30 %) : synchronisation "
                                   f"arrêtée par prudence. Vérifier le chemin et l'accès, puis relancer avec l'option "
                                   f"d'autorisation des suppressions si c'est voulu.")
            # Empreintes des fichiers disparus : un nouveau fichier de même empreinte est un déplacement.
            par_empreinte = {connus[c].sha256: c for c in disparus if connus[c].sha256}
            ordre = sorted(presents, key=_priorite)
            for i, rel in enumerate(ordre, 1):
                if duree_max_min and time.time() - t0 > duree_max_min * 60:
                    cr.statut = "partiel"
                    cr.message = f"Durée maximale atteinte : {len(ordre) - i + 1} fichiers restent pour le prochain passage."
                    break
                if i % 20 == 0 or i == len(ordre):
                    self.etat.battre()
                    self.etat.ecrire(progression=[i, len(ordre)], fichier=rel)
                if suivi:
                    suivi(i, len(ordre), rel)
                p = presents[rel]
                try:
                    self._traiter(racine, rel, p, connus, par_empreinte, disparus, forcer, rafraichir_droits, cr)
                except Exception as e:  # un fichier abîmé ne bloque pas les autres
                    cr.erreurs.append(f"{rel} : {type(e).__name__} {str(e)[:200]}")
            if cr.statut != "partiel":
                for rel in sorted(disparus):
                    self.entrepot.supprimer(rel)
                    cr.supprimes += 1
                cr.statut = "termine"
        except Exception as e:
            cr.statut, cr.message = "echec", str(e)
        finally:
            cr.fin = datetime.now().isoformat(timespec="seconds")
            if not cr.message:
                cr.message = cr.resume()
            valeurs = {"statut": cr.statut, "fin": cr.fin, "compte_rendu": asdict(cr), "fichier": ""}
            if cr.statut in ("termine", "partiel"):
                valeurs["dernier_succes"] = cr.fin
                valeurs["nb_fichiers"] = cr.vus
            self.etat.ecrire(**valeurs)
            self.etat.journaliser(cr)
            self.etat.rendre()
        return cr

    def _traiter(self, racine, rel, p, connus, par_empreinte, disparus, forcer, rafraichir_droits, cr) -> None:
        connu = connus.get(rel)
        st = p.stat()
        if connu and not forcer and connu.taille == st.st_size and connu.mtime == int(st.st_mtime):
            cr.inchanges += 1
            if rafraichir_droits and not connu.exclu:
                self._verifier_droits(rel, p, connu, cr)
            return
        sha = empreinte(p)
        if connu and not forcer and connu.sha256 == sha:  # date changée, contenu identique
            self.entrepot.maj_date(rel, int(st.st_mtime))
            cr.inchanges += 1
            if rafraichir_droits and not connu.exclu:
                self._verifier_droits(rel, p, connu, cr)
            return
        ancien = par_empreinte.pop(sha, None) if not connu and not forcer else None
        if ancien:
            doc = DocumentRAG(chemin=rel, sha256=sha, taille=st.st_size, mtime=int(st.st_mtime))
            m = metadonnees(racine, p, "", self.config.motif_valides)
            doc.affaire, doc.annee, doc.titre, doc.type_ouvrage = m.affaire, m.annee, m.titre, m.type_ouvrage
            if connus[ancien].type_doc == "memoire":
                doc.valide = bool(re.search(self.config.motif_valides, normaliser(rel), re.I))
            doc.comptes = self.droits.comptes(p)
            self.entrepot.deplacer(ancien, doc)
            disparus.discard(ancien)
            cr.deplaces += 1
            return
        doc, secs, bl = self._document(racine, rel, p, st, sha)
        vecteurs = self._vecteurs(bl)
        self.entrepot.enregistrer(doc, secs, bl, vecteurs)
        if doc.exclu:
            cr.exclus += 1
        elif not bl:
            cr.illisibles += 1
        else:
            cr.blocs_ajoutes += len(bl)
            if connu:
                cr.modifies += 1
            else:
                cr.ajoutes += 1

    def _verifier_droits(self, rel: str, p: Path, connu: DocumentRAG, cr: CompteRendu) -> None:
        comptes = self.droits.comptes(p)
        if normaliser_comptes(comptes) != normaliser_comptes(connu.comptes):
            self.entrepot.maj_comptes(rel, comptes)
            cr.droits_modifies += 1
