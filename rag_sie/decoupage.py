"""Découpage des documents d'archive en sections (chapitres) puis en blocs.

Un mémoire technique se lit par chapitre : « 3 Moyens humains > 3.2 Habilitations ».
On garde donc, pour chaque bloc, le chemin des titres au-dessus de lui. Il sert :
- à la recherche (les mots des titres comptent) ;
- à l'affichage (l'utilisateur voit d'où vient le passage) ;
- à l'« élargissement » : on peut renvoyer la section entière autour d'un bloc trouvé.

Les titres sont reconnus :
- dans les fichiers Word, par le style du paragraphe (Titre 1, Heading 2…), voir lire_docx_titres ;
- ailleurs, par leur forme (« 3.2 Habilitations », « CHAPITRE 2 - … », ligne courte en majuscules).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .texte import nettoyer

# Marqueur posé par lire_docx_titres : « ## Titre » = titre de niveau 2.
_MARQUE = re.compile(r"^(#{1,6})\s+(\S.{0,150})$")
_NUMERO = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s*[.)\-–]?\s+([A-ZÀ-Ýa-zà-ý«(][^\n]{2,110})$")
_CHAPITRE = re.compile(r"^\s*(?:chapitre|partie|titre)\s+([IVX]+|\d{1,2})\b\s*[.:\-–]?\s*(.{0,110})$", re.I)
_ROMAIN = re.compile(r"^\s*([IVX]{1,5})\s*[.)\-–]\s+([A-ZÀ-Ý][^\n]{2,110})$")
_MAJUSCULES = re.compile(r"^[A-ZÀ-Ý][A-ZÀ-Ý0-9 '’,()/&\-–]{5,90}$")


@dataclass
class Section:
    id: str
    titres: list[str]
    page: int
    paragraphes: list[tuple[int, str]] = field(default_factory=list)

    @property
    def chemin(self) -> str:
        return " > ".join(self.titres)

    @property
    def texte(self) -> str:
        return "\n".join(p for _, p in self.paragraphes)


@dataclass
class Bloc:
    section_id: str
    ordre: int
    page: int
    chemin: str
    texte: str

    @property
    def texte_indexe(self) -> str:
        """Texte utilisé pour la recherche : titres + contenu."""
        return f"{self.chemin}\n{self.texte}" if self.chemin else self.texte


def titre_de_ligne(ligne: str) -> tuple[int, str] | None:
    """(niveau, titre) si la ligne ressemble à un titre, sinon None."""
    ligne = ligne.strip()
    if not ligne or len(ligne) > 150:
        return None
    m = _MARQUE.match(ligne)
    if m:
        return len(m.group(1)), m.group(2).strip()
    m = _CHAPITRE.match(ligne)
    if m:
        return 1, ligne
    m = _NUMERO.match(ligne)
    if m:
        titre = m.group(2).strip()
        if (titre.endswith((".", ";", ",", ":")) and not titre.endswith("...")) or len(titre.split()) > 14:
            return None
        if re.search(r"\d\s*(?:%|€|eur|points?|jours?|mois|ml|m2|m²|kg)\b", titre, re.I):
            return None  # ligne de tableau ou de barème
        return m.group(1).count(".") + 1, ligne
    m = _ROMAIN.match(ligne)
    if m:
        return 1, ligne
    if _MAJUSCULES.match(ligne) and 2 <= len(ligne.split()) <= 10 and sum(c.isdigit() for c in ligne) < 4:
        return 1, ligne
    return None


def _paragraphes(texte: str) -> list[str]:
    """Regroupe les lignes en paragraphes (ligne vide, puce ou titre = nouveau paragraphe)."""
    out, courant = [], []
    for ligne in texte.splitlines():
        s = ligne.strip()
        if not s:
            if courant:
                out.append(" ".join(courant))
                courant = []
            continue
        if titre_de_ligne(s) or re.match(r"^[-•*–]\s", s):
            if courant:
                out.append(" ".join(courant))
                courant = []
        courant.append(s)
        if titre_de_ligne(s):
            out.append(" ".join(courant))
            courant = []
    if courant:
        out.append(" ".join(courant))
    return out


def sections(pages: list[tuple[int, str]], titre_document: str = "") -> list[Section]:
    pile: list[tuple[int, str]] = []
    out: list[Section] = []
    courante = Section("0", [titre_document] if titre_document else [], pages[0][0] if pages else 1)
    out.append(courante)
    for num, texte in pages:
        for para in _paragraphes(texte):
            t = titre_de_ligne(para)
            if t:
                niveau, titre = t
                while pile and pile[-1][0] >= niveau:
                    pile.pop()
                pile.append((niveau, titre.lstrip("# ").strip()))
                courante = Section(str(len(out)), [x[1] for x in pile], num)
                out.append(courante)
            else:
                courante.paragraphes.append((num, para))
    return [s for s in out if s.paragraphes]


def _decouper_long(mots: list[str], taille: int, recouvrement: int) -> list[str]:
    pas = max(taille - recouvrement, 1)
    return [" ".join(mots[i:i + taille]) for i in range(0, max(len(mots) - recouvrement, 1), pas)]


def blocs(secs: list[Section], taille: int = 300, recouvrement: int = 40, minimum: int = 8) -> list[Bloc]:
    """Blocs d'environ `taille` mots, coupés entre deux paragraphes quand c'est possible."""
    out: list[Bloc] = []
    for s in secs:
        tampon: list[str] = []
        page = s.paragraphes[0][0]
        n = 0

        def vider():
            nonlocal tampon, n
            texte = " ".join(tampon).strip()
            if len(texte.split()) >= minimum:
                out.append(Bloc(s.id, n, page, s.chemin, texte))
                n += 1
            tampon = []

        for num, para in s.paragraphes:
            mots = para.split()
            if len(mots) > taille:  # paragraphe très long : fenêtres qui se recouvrent
                vider()
                for morceau in _decouper_long(mots, taille, recouvrement):
                    tampon, page = [morceau], num
                    vider()
                continue
            if sum(len(x.split()) for x in tampon) + len(mots) > taille:
                vider()
            if not tampon:
                page = num
            tampon.append(para)
        vider()
    return out


def lire_docx_titres(chemin: Path) -> list[tuple[int, str]]:
    """Lit un .docx en marquant les titres d'après leur style (« ## Titre » pour un titre de niveau 2).

    Word n'a pas de pages fiables : on compte une « page » toutes les 40 lignes, pour situer le passage.
    """
    import docx

    d = docx.Document(str(chemin))
    lignes: list[str] = []
    for p in d.paragraphs:
        texte = p.text.strip()
        if not texte:
            lignes.append("")
            continue
        style = (p.style.name if p.style is not None else "") or ""
        m = re.match(r"(?:heading|titre)\s*(\d)", style, re.I)
        if m:
            lignes.append("")
            lignes.append("#" * int(m.group(1)) + " " + texte)
        elif style.lower() in ("title", "titre"):
            lignes.append("# " + texte)
        else:
            lignes.append(texte)
    for table in d.tables:
        lignes.append("")
        for row in table.rows:
            lignes.append(" | ".join(c.text.strip() for c in row.cells))
    pages = []
    for i in range(0, max(len(lignes), 1), 40):
        pages.append((i // 40 + 1, nettoyer("\n".join(lignes[i:i + 40]))))
    return pages
