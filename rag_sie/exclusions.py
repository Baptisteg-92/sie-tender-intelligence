"""Documents écartés.

Deux niveaux :
- jamais indexés : données personnelles sans intérêt pour un mémoire (CV, pièces d'identité,
  paie, dossiers RH). Ils ne sont ni lus ni stockés ; seul leur chemin est noté pour ne pas
  les relire à chaque synchronisation ;
- indexés mais jamais envoyés à une IA extérieure : les prix (DPGF, BPU, DQE, devis).
  On peut les retrouver dans la recherche interne, pas les transmettre.
"""
from __future__ import annotations

import re
from pathlib import Path

from .texte import normaliser

JAMAIS_INDEXES = [
    (r"\bcv\b|curriculum", "CV"),
    (r"passeport|carte (?:nationale )?d.?identite|\bcni\b|titre de sejour|permis de conduire", "pièce d'identité"),
    (r"bulletin(?:s)? de (?:paie|salaire)|fiche(?:s)? de paie|\brib\b|releve d.?identite bancaire", "paie ou banque"),
    (r"arret (?:de travail|maladie)|visite medicale|aptitude medicale|mutuelle|dossier (?:du )?personnel|entretien annuel", "RH ou santé"),
]
JAMAIS_ENVOYES = [
    (r"\bdpgf\b|decomposition du prix|\bbpu\b|bordereau des prix|\bdqe\b|detail quantitatif|\bdevis\b|chiffrage|sous-detail", "prix"),
]


def _cherche(motifs, texte: str) -> str | None:
    for motif, raison in motifs:
        if re.search(motif, texte):
            return raison
    return None


def raison_exclusion(chemin: str | Path, supplementaires: list[str] | None = None) -> str | None:
    """Raison pour laquelle le fichier ne doit jamais être indexé, d'après son chemin."""
    n = normaliser(str(chemin).replace("_", " ").replace("-", " ").replace("\\", "/"))
    r = _cherche(JAMAIS_INDEXES, n)
    if r:
        return r
    for motif in supplementaires or []:
        if re.search(motif, n, re.I):
            return f"règle SIE : {motif}"
    return None


def raison_contenu(debut_texte: str) -> str | None:
    """Même contrôle sur le début du texte (un CV mal nommé, par exemple)."""
    n = normaliser(debut_texte[:1500])
    if re.search(r"curriculum vitae|\bexperiences? professionnelles?\b.*\bformation\b", n):
        return "CV"
    return _cherche(JAMAIS_INDEXES[1:3], n)


def envoyable(chemin: str | Path, type_doc: str) -> bool:
    """Un passage de ce document peut-il être transmis à l'IA de rédaction ?"""
    if type_doc == "prix":
        return False
    return _cherche(JAMAIS_ENVOYES, normaliser(str(chemin).replace("_", " "))) is None
