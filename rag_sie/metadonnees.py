"""Métadonnées d'un document d'archive : affaire, année, type, activité, type d'ouvrage, statut.

Organisation attendue (à confirmer avec SIE) : un dossier par affaire sous la racine.

    \\\\serveur\\memoires\\2023_college_jean_moulin\\memoire_technique_definitif.docx

Si le dossier d'affaire contient un affaire.json, ses valeurs priment :
    {"annee": 2023, "activite": ["CFO", "SSI"], "type_ouvrage": "enseignement", "resultat": "gagné"}
Sinon, les valeurs sont déduites du nom et du texte, et restent indicatives.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .texte import normaliser

ACTIVITES = {
    "CFO": r"courants? forts?|\bcfo\b|tgbt|tableau general basse tension|distribution electrique|eclairage|prises de courant|liaisons? equipotentielles?|groupe electrogene",
    "CFA": r"courants? faibles?|\bcfa\b|\bvdi\b|baie de brassage|controle d.?acces|videosurveillance|videoprotection|interphonie|intrusion",
    "SSI": r"\bssi\b|securite incendie|alarme incendie|detection incendie|\bcmsi\b|\bsdi\b|desenfumage",
    "CVC": r"\bcvc\b|chauffage|ventilation|climatisation|\bvmc\b|\bcta\b|pompe a chaleur",
    "Plomberie": r"plomberie|sanitaires?|eau chaude sanitaire|\becs\b|evacuations? eaux",
}
TYPES_OUVRAGE = {
    "enseignement": r"ecole|college|lycee|groupe scolaire|universite|creche|maternelle|enseignement",
    "santé": r"hopital|ehpad|clinique|centre hospitalier|maison de sante|medico",
    "logement": r"logements?|residence|habitat|bailleur",
    "bureaux": r"bureaux|tertiaire|siege social",
    "sport": r"gymnase|piscine|stade|complexe sportif|salle de sport",
    "culture": r"mediatheque|bibliotheque|theatre|musee|conservatoire|cinema",
    "administration": r"mairie|hotel de ville|prefecture|tribunal|caserne|gendarmerie|commissariat",
    "industrie": r"usine|industriel|atelier|entrepot|logistique",
}


@dataclass
class Meta:
    affaire: str
    annee: int | None = None
    type_doc: str = "autre"          # memoire | dce | prix | attestation | reference | autre
    activites: list[str] = field(default_factory=list)
    type_ouvrage: str | None = None
    valide: bool | None = None       # mémoires seulement : validé / remis, ou brouillon
    resultat: str | None = None      # gagné / perdu, si SIE le renseigne
    titre: str = ""


def type_document(nom: str, debut: str) -> str:
    n = normaliser(nom.replace("_", " ").replace("-", " "))
    d = normaliser(debut[:1500])
    for texte in (n, d):
        if re.search(r"memoire|methodologi|note technique|offre technique|dossier technique", texte):
            return "memoire"
        if re.search(r"\bdpgf\b|\bbpu\b|\bdqe\b|decomposition du prix|bordereau des prix|detail quantitatif|\bdevis\b", texte):
            return "prix"
        if re.search(r"\brc\b|\bcctp\b|\bccap\b|\bae\b|reglement de (?:la )?consultation|cahier des clauses|acte d.?engagement", texte):
            return "dce"
        if re.search(r"attestation|certificat|qualifelec|qualibat|kbis|urssaf|assurance", texte):
            return "attestation"
        if re.search(r"references?\b|liste des chantiers", texte):
            return "reference"
    return "autre"


def _detecter(motifs: dict[str, str], texte: str, seuil: int) -> list[str]:
    comptes = {k: len(re.findall(m, texte)) for k, m in motifs.items()}
    return [k for k, c in sorted(comptes.items(), key=lambda x: -x[1]) if c >= seuil]


def metadonnees(racine: Path, chemin: Path, texte: str, motif_valides: str) -> Meta:
    rel = chemin.relative_to(racine)
    affaire = rel.parts[0] if len(rel.parts) > 1 else "(racine)"
    fiche: dict = {}
    if len(rel.parts) > 1 and (racine / affaire / "affaire.json").exists():
        try:
            fiche = json.loads((racine / affaire / "affaire.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            fiche = {}
    m = Meta(affaire=affaire, titre=chemin.stem.replace("_", " "))
    annee = fiche.get("annee")
    if not annee:
        a = re.search(r"(?<!\d)(19[89]\d|20[0-4]\d)(?!\d)", affaire)
        annee = int(a.group(1)) if a else None
    m.annee = int(annee) if annee else None
    m.type_doc = type_document(chemin.name, texte)
    n = normaliser(texte[:30000])
    act = fiche.get("activite") or fiche.get("activites")
    m.activites = ([act] if isinstance(act, str) else list(act)) if act else _detecter(ACTIVITES, n, 3)
    nom = normaliser(affaire.replace("_", " "))
    m.type_ouvrage = fiche.get("type_ouvrage") or fiche.get("type") or (_detecter(TYPES_OUVRAGE, nom, 1) or _detecter(TYPES_OUVRAGE, n[:5000], 2) or [None])[0]
    m.resultat = fiche.get("resultat")
    if m.type_doc == "memoire":
        if "valide" in fiche:
            m.valide = bool(fiche["valide"])
        else:
            m.valide = bool(re.search(motif_valides, normaliser(str(rel)), re.I)) if motif_valides else None
    return m
