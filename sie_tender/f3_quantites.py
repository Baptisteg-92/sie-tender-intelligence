"""F3 : import des quantités relevées par le bureau d'études (fichier Excel SIE).

Aucune quantité n'est calculée à partir des plans : l'outil lit le tableau,
signale les lignes douteuses et laisse le bureau d'études corriger.
"""
from __future__ import annotations

import re
from pathlib import Path

from .models import LigneQuantite, SourceTableur, Statut
from .texte import normaliser

ENTETES = {
    "code": [r"^n[°o]?$", r"^code", r"^ref", r"^article", r"^poste", r"^item", r"^n[°o] ?(?:de )?(?:prix|poste)"],
    "designation": [r"designation", r"libelle", r"description", r"intitule", r"nature des"],
    "unite": [r"^u$", r"^unite", r"^unit"],
    "quantite": [r"^q(?:te|uantite)", r"^qt", r"^nombre", r"^nb"],
    "famille": [r"famille", r"chapitre", r"categorie", r"rubrique"],
    "lot": [r"^lot"],
}

# Familles par mots-clés, quand le tableau n'a pas de colonne « famille ».
FAMILLES = [
    ("Tableaux et protections", r"tableau|tgbt|disjoncteur|interrupteur differentiel|coffret|armoire|parafoudre"),
    ("Câbles et conduits", r"cable|cablage|conduit|goulotte|chemin de cable|icta|u1000|r2v|fil "),
    ("Éclairage", r"luminaire|eclairage|led|hublot|projecteur|spot|bloc autonome|baes|baeh|detecteur de presence"),
    ("Appareillage", r"prise|interrupteur|va-et-vient|bouton|appareillage"),
    ("Sécurité incendie", r"\bssi\b|alarme|detecteur (?:optique|de fumee)|declencheur|desenfumage|sirene|centrale incendie|\bdad\b|\bcmsi\b"),
    ("Courants faibles", r"\bvdi\b|rj45|baie|fibre|telephon|interphon|controle d'acces|video|wifi|sonorisation"),
    ("CVC", r"ventilat|\bcta\b|climatis|radiateur|chaudiere|pompe a chaleur|gaine|vmc|clapet"),
    ("Plomberie", r"plomberie|sanitaire|lavabo|wc|robinet|tube cuivre|per |evacuation"),
    ("Main-d'œuvre et prestations", r"main d'oeuvre|depose|essais|mise en service|doe|etude|forfait|nettoyage"),
]


def famille_de(designation: str) -> str:
    n = normaliser(designation)
    for nom, motif in FAMILLES:
        if re.search(motif, n):
            return nom
    return "Divers"


def _nombre(v) -> float | None:
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _colonnes(ligne: list) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, v in enumerate(ligne):
        n = normaliser(str(v or ""))
        if not n:
            continue
        for cle, motifs in ENTETES.items():
            if cle not in cols and any(re.search(m, n) for m in motifs):
                cols[cle] = i
                break
    return cols


UNITES_CONNUES = {"u", "ens", "ml", "m", "m2", "m3", "ft", "forfait", "kg", "h", "pm", "l", "t", "lot", "pce", "pc", "jour", "j"}
_ALIAS_UNITES = {"unite": "u", "un": "u", "nb": "u", "ensemble": "ens", "f": "ft", "fft": "ft", "m²": "m2", "m³": "m3",
                 "mètre": "m", "metre": "m", "ml.": "ml", "pièce": "pce", "piece": "pce"}


def unite_normalisee(u: str | None) -> str | None:
    if u is None:
        return None
    n = normaliser(str(u)).replace(" ", "").rstrip(".")
    return _ALIAS_UNITES.get(n, n)


def feuilles(chemin: str | Path) -> list[str]:
    chemin = Path(chemin)
    if chemin.suffix.lower() == ".csv":
        return ["CSV"]
    if chemin.suffix.lower() == ".xls":
        import pandas as pd
        return list(pd.ExcelFile(chemin).sheet_names)
    from openpyxl import load_workbook
    wb = load_workbook(chemin, read_only=True)
    return [ws.title for ws in wb.worksheets]


def _rangees(chemin: Path, feuille: str | None) -> list[tuple[str, list[list]]]:
    """(nom de feuille, lignes) pour une feuille ou pour toutes. CSV : séparateur ; ou , détecté."""
    suffixe = chemin.suffix.lower()
    if suffixe == ".csv":
        import csv
        brut = chemin.read_bytes()
        texte = next((brut.decode(enc) for enc in ("utf-8-sig", "cp1252", "latin-1") if _decodable(brut, enc)), "")
        dialecte = csv.Sniffer().sniff(texte[:4000], delimiters=";,\t") if texte.strip() else csv.excel
        return [("CSV", [list(r) for r in csv.reader(texte.splitlines(), dialecte)])]
    if suffixe == ".xls":
        import pandas as pd
        xl = pd.ExcelFile(chemin)
        noms = [feuille] if feuille else xl.sheet_names
        return [(n, xl.parse(n, header=None).where(lambda d: d.notna(), None).values.tolist()) for n in noms]
    from openpyxl import load_workbook
    wb = load_workbook(chemin, data_only=True, read_only=True)
    feuilles_ = [wb[feuille]] if feuille else wb.worksheets
    return [(ws.title, [list(r) for r in ws.iter_rows(values_only=True)]) for ws in feuilles_]


def _decodable(brut: bytes, enc: str) -> bool:
    try:
        brut.decode(enc)
        return True
    except UnicodeDecodeError:
        return False


def apercu(chemin: str | Path, feuille: str | None = None) -> tuple[list[str], dict[str, int], int | None]:
    """En-têtes trouvés, correspondance proposée (rôle -> n° de colonne) et ligne d'en-tête, pour l'écran de mappage."""
    nom, rangees = _rangees(Path(chemin), feuille)[0]
    for i, r in enumerate(rangees[:30]):
        c = _colonnes(r)
        if "designation" in c:
            return [str(v) if v not in (None, "") else f"Colonne {k + 1}" for k, v in enumerate(r)], c, i
    largeur = max((len(r) for r in rangees[:30]), default=0)
    return [f"Colonne {k + 1}" for k in range(largeur)], {}, None


def importer_quantites(chemin: str | Path, lot_defaut: str | None = None, feuille: str | None = None,
                       colonnes: dict[str, int] | None = None, ligne_entete: int | None = None) -> tuple[list[LigneQuantite], list[str]]:
    """Lit une feuille (ou toutes) d'un classeur XLSX/XLS ou d'un CSV ; renvoie les lignes et un journal.

    colonnes : correspondance choisie par l'utilisateur (rôle -> n° de colonne), sinon détectée sur les en-têtes.
    """
    chemin = Path(chemin)
    journal: list[str] = []
    lignes: list[LigneQuantite] = []
    for titre, rangees in _rangees(chemin, feuille):
        cols, debut = {}, None
        if colonnes:
            cols, debut = dict(colonnes), (ligne_entete + 1 if ligne_entete is not None else 0)
        else:
            for i, r in enumerate(rangees[:30]):
                c = _colonnes(list(r))
                if "designation" in c and "quantite" in c:
                    cols, debut = c, i + 1
                    break
        if debut is None or "designation" not in cols or "quantite" not in cols:
            journal.append(f"F3 : feuille « {titre} » ignorée (pas de colonnes Désignation / Quantité).")
            continue
        m = re.search(r"lot\s*(\d{1,2})", normaliser(titre))
        lot_feuille = str(int(m.group(1))) if m else lot_defaut
        famille_courante = None
        for j, r in enumerate(rangees[debut:], start=debut + 1):
            r = list(r) + [None] * 20
            des = r[cols["designation"]]
            if des is None or not str(des).strip():
                continue
            des = re.sub(r"\s+", " ", str(des)).strip()
            qte_brute = r[cols["quantite"]]
            qte = _nombre(qte_brute)
            unite = str(r[cols["unite"]]).strip() if "unite" in cols and r[cols["unite"]] not in (None, "") else None
            # Ligne de chapitre : texte sans quantité ni unité
            if qte is None and unite is None and qte_brute in (None, ""):
                famille_courante = des
                continue
            l = LigneQuantite(
                lot=str(r[cols["lot"]]).strip() if "lot" in cols and r[cols["lot"]] else lot_feuille,
                code=str(r[cols["code"]]).strip() if "code" in cols and r[cols["code"]] not in (None, "") else None,
                famille=(str(r[cols["famille"]]).strip() if "famille" in cols and r[cols["famille"]] else None) or famille_courante or famille_de(des),
                designation=des, unite=unite, quantite=qte,
                source=SourceTableur(fichier=chemin.name, feuille=titre, ligne=j),
            )
            problemes = []
            if qte is None:
                problemes.append(f"quantité illisible « {qte_brute} »" if qte_brute not in (None, "") else "quantité manquante")
            elif qte < 0:
                problemes.append("quantité négative")
            elif qte == 0:
                problemes.append("quantité nulle")
            if unite is None:
                problemes.append("unité manquante")
            elif unite_normalisee(unite) not in UNITES_CONNUES:
                problemes.append(f"unité ambiguë « {unite} »")
            if problemes:
                l.statut = Statut.A_VERIFIER
                l.remarque = ", ".join(problemes)
            lignes.append(l)
    _doublons(lignes)
    a_verif = sum(1 for l in lignes if l.statut == Statut.A_VERIFIER)
    journal.append(f"F3 : {len(lignes)} lignes importées depuis {chemin.name}, dont {a_verif} à vérifier.")
    return lignes, journal


def _doublons(lignes: list[LigneQuantite]) -> None:
    vus: dict[tuple, LigneQuantite] = {}
    for l in lignes:
        cle = (l.lot, l.code) if l.code else (l.lot, normaliser(l.designation), unite_normalisee(l.unite))
        if cle in vus:
            premier = vus[cle]
            msg = f"doublon possible avec la ligne {premier.source.ligne if premier.source else '?'}"
            l.statut = Statut.A_VERIFIER
            l.remarque = f"{l.remarque}, {msg}" if l.remarque else msg
        else:
            vus[cle] = l


def controler_metre(lignes: list[LigneQuantite]) -> dict[str, int]:
    """Compte des anomalies, pour l'écran de prévisualisation."""
    rem = [l.remarque or "" for l in lignes]
    return {"doublons": sum("doublon" in r for r in rem), "unites": sum("unité" in r for r in rem),
            "quantites": sum("quantité" in r for r in rem), "total": len(lignes)}
