"""Petits outils texte (copiés ici pour que le paquet reste autonome)."""
from __future__ import annotations

import re
import unicodedata


def sans_accents(texte: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texte) if unicodedata.category(c) != "Mn")


def normaliser(texte: str) -> str:
    """Minuscules, sans accents, espaces simples : sert aux comparaisons."""
    texte = sans_accents(texte).lower().replace("’", "'").replace(" ", " ")
    return re.sub(r"\s+", " ", texte).strip()


def nettoyer(texte: str) -> str:
    texte = texte.replace(" ", " ").replace("’", "'")
    texte = re.sub(r"-\n(\w)", r"\1", texte)  # césures en fin de ligne
    return re.sub(r"[ \t]+", " ", texte)


MOTS_VIDES = set("""a au aux avec ce ces dans de des du elle en et eux il je la le les leur lui ma mais me meme mes moi mon
ne nos notre nous on ou par pas pour qu que qui sa se ses son sur ta te tes toi ton tu un une vos votre vous c d j l m n s t y
ete etre est sont sera seront ont avoir fait faire cette cet leurs tout tous toute toutes plus moins tres ainsi afin lors
entre sans sous chez dont donc car si""".split())


def jetons(texte: str) -> list[str]:
    """Mots utiles, sans accents, avec une racinisation légère (pluriels, suffixes courants)."""
    out = []
    for m in re.findall(r"[a-z0-9]+", normaliser(texte)):
        if m in MOTS_VIDES or len(m) < 2:
            continue
        for suf in ("ements", "ement", "ations", "ation", "ites", "ite", "eurs", "eur", "es", "s"):
            if len(m) > len(suf) + 3 and m.endswith(suf):
                m = m[: -len(suf)]
                break
        out.append(m)
    return out
