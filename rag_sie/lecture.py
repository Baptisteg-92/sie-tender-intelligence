"""Lecture des fichiers d'archive : texte par page, titres repérés, OCR des scans.

- PDF : texte natif avec PyMuPDF ; pages scannées passées à l'OCR Tesseract (langue fra) si installé.
  Si Docling est installé et SIE_RAG_DOCLING=1, il est utilisé à la place : il reconnaît mieux
  les titres et les tableaux des PDF (et fait lui-même l'OCR).
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


def _ocr_page(page) -> str | None:
    if shutil.which("tesseract") is None:
        return None
    try:
        tp = page.get_textpage_ocr(language="fra", dpi=300, full=True)
        return page.get_text("text", textpage=tp)
    except Exception:
        return None


def _pdf_pymupdf(chemin: Path) -> Lu:
    import pymupdf

    lu = Lu()
    with pymupdf.open(chemin) as doc:
        for i, page in enumerate(doc, start=1):
            texte = page.get_text("text")
            if len(texte.strip()) < 20 and page.get_images():
                texte_ocr = _ocr_page(page)
                if texte_ocr is None:
                    lu.remarque = "pages scannées non lues : OCR indisponible (installer Tesseract + langue fra)"
                else:
                    texte, lu.ocr = texte_ocr, True
            lu.pages.append((i, nettoyer(texte)))
    return lu


def _pdf_docling(chemin: Path) -> Lu | None:
    try:
        from docling.document_converter import DocumentConverter
    except ImportError:
        return None
    try:
        md = DocumentConverter().convert(str(chemin)).document.export_to_markdown()
    except Exception:
        return None
    lignes = md.splitlines()
    return Lu([(i // 60 + 1, nettoyer("\n".join(lignes[i:i + 60]))) for i in range(0, max(len(lignes), 1), 60)])


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
        if os.getenv("SIE_RAG_DOCLING") == "1":
            lu = _pdf_docling(chemin)
            if lu is not None and lu.lisible:
                return lu
        return _pdf_pymupdf(chemin)
    if ext == ".docx":
        return Lu(lire_docx_titres(chemin))
    if ext in (".xlsx", ".xlsm"):
        return _xlsx(chemin)
    if ext in (".txt", ".md"):
        return Lu([(1, nettoyer(chemin.read_text(encoding="utf-8", errors="replace")))])
    return Lu(remarque=f"format {ext or 'inconnu'} non lu")
