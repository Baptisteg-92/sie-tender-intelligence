"""Lecture et découpage des PDF, sans PyMuPDF (licence AGPL écartée le 26/11/2026).

    lire_pdf(chemin)        -> [(n° de page, texte, ocr?)], remarque
    nb_pages(chemin)        -> nombre de pages
    extraire_pages(...)     -> nouveau PDF avec les seules pages choisies

Lecture : Docling (licence MIT) en priorité, qui repère titres et tableaux et fait l'OCR des scans (Tesseract, fra).
Si Docling n'est pas installé ou échoue sur un fichier, on se rabat sur pypdfium2 (Apache / BSD) pour le texte
natif, et Tesseract en ligne de commande pour les pages scannées. SIE_DOCLING=0 force la lecture rapide
(utile pour les essais : Docling est nettement plus lent sur processeur).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_CONVERTISSEUR = None


def _docling():
    """Convertisseur Docling, créé une seule fois (chargement des modèles coûteux). None si indisponible."""
    global _CONVERTISSEUR
    if _CONVERTISSEUR is not None or os.getenv("SIE_DOCLING", "1") == "0":
        return _CONVERTISSEUR or None
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError:
        return None
    options = PdfPipelineOptions()
    options.do_ocr = True
    options.do_table_structure = True
    if shutil.which("tesseract"):
        try:
            from docling.datamodel.pipeline_options import TesseractCliOcrOptions
            options.ocr_options = TesseractCliOcrOptions(lang=["fra"])
        except ImportError:
            pass
    _CONVERTISSEUR = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
    return _CONVERTISSEUR


def _par_docling(chemin: Path) -> list[tuple[int, str, bool]] | None:
    conv = _docling()
    if conv is None:
        return None
    try:
        doc = conv.convert(str(chemin)).document
        n = doc.num_pages() if callable(getattr(doc, "num_pages", None)) else len(getattr(doc, "pages", {}) or {})
        pages = [(i, doc.export_to_markdown(page_no=i), False) for i in range(1, n + 1)]
    except Exception:
        return None
    return pages if any(t.strip() for _, t, _ in pages) else None


def _texte_page(page) -> str:
    tp = page.get_textpage()
    try:
        texte = tp.get_text_bounded() if hasattr(tp, "get_text_bounded") else tp.get_text_range()
        return texte.replace("\r\n", "\n").replace("\r", "\n")  # pdfium rend des fins de ligne Windows
    finally:
        tp.close()


def _ocr(page) -> str | None:
    if shutil.which("tesseract") is None:
        return None
    try:
        image = page.render(scale=300 / 72).to_pil()
        with tempfile.TemporaryDirectory() as d:
            png = Path(d) / "page.png"
            image.save(png)
            r = subprocess.run(["tesseract", str(png), "stdout", "-l", "fra"], capture_output=True, text=True, timeout=300)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def _par_pdfium(chemin: Path) -> tuple[list[tuple[int, str, bool]], str | None]:
    import pypdfium2 as pdfium

    pages, remarque = [], None
    pdf = pdfium.PdfDocument(str(chemin))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            texte, ocr = _texte_page(page), False
            if len(texte.strip()) < 20:
                lu = _ocr(page)
                if lu is None:
                    remarque = "Pages scannées sans texte : OCR non disponible (installer Tesseract avec la langue fra)."
                elif lu.strip():
                    texte, ocr = lu, True
            pages.append((i + 1, texte, ocr))
            page.close()
    finally:
        pdf.close()
    return pages, remarque


def lire_pdf(chemin: str | Path) -> tuple[list[tuple[int, str, bool]], str | None]:
    chemin = Path(chemin)
    pages = _par_docling(chemin)
    if pages is not None:
        return pages, None
    return _par_pdfium(chemin)


def nb_pages(chemin: str | Path) -> int:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(str(chemin))
    try:
        return len(pdf)
    finally:
        pdf.close()


def extraire_pages(source: str | Path, plages: list[tuple[int, int]], sortie: str | Path) -> Path:
    """Nouveau PDF ne contenant que les plages choisies (numéros à partir de 1), dans l'ordre donné."""
    import pypdfium2 as pdfium

    src = pdfium.PdfDocument(str(source))
    dst = pdfium.PdfDocument.new()
    try:
        for debut, fin in plages:
            dst.import_pages(src, list(range(debut - 1, min(fin, len(src)))), index=len(dst))
        dst.save(str(sortie))
    finally:
        dst.close()
        src.close()
    return Path(sortie)
