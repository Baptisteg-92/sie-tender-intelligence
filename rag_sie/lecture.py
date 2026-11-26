"""Lecture des fichiers d'archive : texte par page, titres repérés, OCR des scans.

- PDF : Docling (titres, tableaux, OCR) s'il est installé, sinon pypdfium2 pour le texte natif et Tesseract
  (langue fra) pour les pages scannées. Voir pdf.py.
- Word (.docx) : paragraphes avec leurs styles de titre.
- Excel : une « page » par feuille, une ligne par rangée.
- Texte / Markdown : tel quel.

Tout est local : aucun service extérieur, aucune API d'OCR payante.
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .decoupage import lire_docx_titres
from .texte import nettoyer


@dataclass
class Lu:
    pages: list[tuple[int, str]] = field(default_factory=list)
    ocr: bool = False
    remarque: str | None = None

    @property
    def lisible(self) -> bool:
        return any(t.strip() for _, t in self.pages)


def _pdf(chemin: Path) -> Lu:
    from .pdf import lire_pdf

    lues, remarque = lire_pdf(chemin)
    return Lu([(n, nettoyer(t)) for n, t, _ in lues], ocr=any(o for _, _, o in lues), remarque=remarque)


def _xlsx(chemin: Path) -> Lu:
    from openpyxl import load_workbook

    wb = load_workbook(chemin, read_only=True, data_only=True)
    lu = Lu()
    for i, ws in enumerate(wb.worksheets, start=1):
        lignes = [" | ".join(str(c) for c in row if c is not None) for row in ws.iter_rows(values_only=True)]
        lu.pages.append((i, f"# Feuille {ws.title}\n" + "\n".join(x for x in lignes if x)))
    return lu


def lire(chemin: Path) -> Lu:
    ext = chemin.suffix.lower()
    if ext == ".pdf":
        return _pdf(chemin)
    if ext == ".docx":
        return Lu(lire_docx_titres(chemin))
    if ext in (".xlsx", ".xlsm"):
        return _xlsx(chemin)
    if ext in (".txt", ".md"):
        return Lu([(1, nettoyer(chemin.read_text(encoding="utf-8", errors="replace")))])
    return Lu(remarque=f"format {ext or 'inconnu'} non lu")
