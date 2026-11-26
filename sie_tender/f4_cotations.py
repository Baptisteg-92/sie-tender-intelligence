"""F4 : brouillons de demandes de cotation fournisseurs.

Les lignes de quantités validées sont regroupées par lot et par famille. Pour
chaque ligne, l'outil cite les phrases du CCTP qui parlent du même matériel
(caractéristiques), avec leur source. Le choix des fournisseurs, la relecture
et l'envoi restent à SIE.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from datetime import date, timedelta
from pathlib import Path

from .export import ref
from .f6_archives import jetons
from .models import (CategorieExigence, Consultation, DemandeCotation, Exigence, LigneCotation)
from .texte import normaliser

_MOIS = {m: i for i, m in enumerate(["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout",
                                       "septembre", "octobre", "novembre", "decembre"], start=1)}


def lire_date(texte: str | None) -> date | None:
    if not texte:
        return None
    n = normaliser(texte)
    m = re.search(r"(\d{1,2})(?:er)?\s+([a-z]+)\s+(\d{4})", n)
    if m and m.group(2) in _MOIS:
        return date(int(m.group(3)), _MOIS[m.group(2)], int(m.group(1)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", n)
    if m:
        an = int(m.group(3))
        return date(an + 2000 if an < 100 else an, int(m.group(2)), int(m.group(1)))
    return None


def _caracteristiques(designation: str, exigences: list[Exigence], lot: str | None, k: int = 2) -> list[str]:
    mots = {j for j in jetons(designation) if len(j) > 3 and not j.isdigit()}
    if not mots:
        return []
    notes = []
    for e in exigences:
        if e.categorie not in (CategorieExigence.TECHNIQUE, CategorieExigence.INTERFACE, CategorieExigence.RSE):
            continue
        if lot and e.lot not in (lot, None):
            continue
        commun = mots & set(jetons(e.texte))
        if commun:
            notes.append((len(commun), f"{e.texte} [{ref(e.source)}]"))
    notes.sort(key=lambda x: -x[0])
    return [t for _, t in notes[:k]]


def generer_demandes(c: Consultation, delai_retour_jours: int = 10) -> list[DemandeCotation]:
    """Une demande par (lot, famille), à partir des quantités sans erreur bloquante."""
    groupes: "OrderedDict[tuple, list]" = OrderedDict()
    for q in c.quantites:
        if q.quantite is None or q.quantite <= 0:
            continue
        groupes.setdefault((q.lot, q.famille or "Divers"), []).append(q)
    limite = lire_date(c.fiche.date_limite_remise.valeur)
    echeance = (limite - timedelta(days=delai_retour_jours)).strftime("%d/%m/%Y") if limite else None
    pieces_utiles = [p.fichier for p in c.pieces if p.type.value in ("CCTP", "PLAN") ]
    demandes = []
    for (lot, famille), qs in groupes.items():
        if famille.lower().startswith("main-d"):
            continue  # la main-d'œuvre n'est pas consultée chez les fournisseurs
        d = DemandeCotation(lot=lot, famille=famille, echeance_retour=echeance,
                            pieces_utiles=[f for f in pieces_utiles if lot is None or f"lot{int(lot):02d}" in normaliser(f).replace(" ", "") or f"lot{lot}" in normaliser(f).replace(" ", "")] or pieces_utiles)
        for q in qs:
            d.lignes.append(LigneCotation(ligne_quantite_id=q.id, designation=q.designation, unite=q.unite, quantite=q.quantite,
                                          caracteristiques=_caracteristiques(q.designation, c.exigences, lot)))
        demandes.append(d)
    if not echeance:
        c.journal.append("F4 : date limite de remise inconnue, échéance de retour des devis à fixer par SIE.")
    return demandes


def texte_courriel(c: Consultation, d: DemandeCotation) -> str:
    objet = c.fiche.objet.valeur or c.fiche.intitule.valeur or "consultation en cours"
    lignes = "\n".join(f"- {l.designation} : {l.quantite:g} {l.unite or ''}".rstrip() for l in d.lignes)
    return (f"Objet : Demande de prix - {objet} - lot {d.lot or '?'} - {d.famille}\n\n"
            f"Madame, Monsieur,\n\nDans le cadre de notre réponse à la consultation « {objet} », "
            f"nous vous remercions de nous adresser votre meilleure offre de prix pour les fournitures suivantes :\n\n"
            f"{lignes}\n\nLes caractéristiques demandées figurent dans le tableau joint, avec les extraits du CCTP."
            f"\nMerci de nous retourner votre offre avant le {d.echeance_retour or '[date à fixer]'}.\n\n"
            f"Cordialement,\n[Nom, SIE]\n\n[BROUILLON : à relire par SIE avant envoi]")


def exporter_demandes(c: Consultation, chemin: str | Path) -> Path:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    wb.remove(wb.active)
    for i, d in enumerate(c.demandes_cotation, start=1):
        nom = re.sub(r"[\\/*?:\[\]]", "", f"{i:02d} L{d.lot or '-'} {d.famille}")[:31]
        ws = wb.create_sheet(nom)
        ws.append([f"Demande de prix - lot {d.lot or '?'} - {d.famille}"])
        ws["A1"].font = Font(bold=True, size=13)
        ws.append([f"Retour souhaité avant le : {d.echeance_retour or 'à fixer par SIE'}"])
        ws.append([f"Pièces jointes utiles : {', '.join(d.pieces_utiles)}"])
        ws.append([])
        ws.append(["Désignation", "Unité", "Quantité", "Caractéristiques (extraits du CCTP)", "Prix unitaire HT", "Délai"])
        for cell in ws[5]:
            cell.font = Font(bold=True)
        for l in d.lignes:
            ws.append([l.designation, l.unite, l.quantite, "\n".join(l.caracteristiques), None, None])
        for col, w in zip("ABCDEF", (40, 8, 10, 80, 16, 12)):
            ws.column_dimensions[col].width = w
        for row in ws.iter_rows(min_row=6):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws2_row = ws.max_row + 2
        ws.cell(row=ws2_row, column=1, value="Brouillon de courriel :").font = Font(bold=True)
        ws.cell(row=ws2_row + 1, column=1, value=texte_courriel(c, d)).alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=ws2_row + 1, start_column=1, end_row=ws2_row + 1, end_column=6)
        ws.row_dimensions[ws2_row + 1].height = 260
    chemin = Path(chemin)
    wb.save(chemin)
    return chemin


# --- Demande ciblée par fournisseur -------------------------------------------------------
def lire_plages(texte: str, nb_pages: int | None = None) -> tuple[list[tuple[int, int]], list[str]]:
    """« 12-15, 38-42, 50 » -> [(12, 15), (38, 42), (50, 50)] ; pages du PDF, pas la pagination imprimée."""
    plages, erreurs = [], []
    for morceau in re.split(r"[;,\s]+", texte.strip()):
        if not morceau:
            continue
        m = re.fullmatch(r"(\d+)(?:\s*[-–]\s*(\d+))?", morceau)
        if not m:
            erreurs.append(f"« {morceau} » n'est pas une plage (ex. 12-15)")
            continue
        debut, fin = int(m.group(1)), int(m.group(2) or m.group(1))
        if debut < 1 or fin < debut:
            erreurs.append(f"plage {morceau} invalide")
        elif nb_pages and fin > nb_pages:
            erreurs.append(f"plage {morceau} : le PDF n'a que {nb_pages} pages")
        else:
            plages.append((debut, fin))
    return plages, erreurs


def extraire_pages(pdf: str | Path, plages: list[tuple[int, int]], sortie: str | Path) -> Path:
    """PDF ne contenant que les plages choisies, dans l'ordre saisi. Les autres pages ne sont pas transmises."""
    from .pdf import extraire_pages as extraire

    return extraire(pdf, plages, Path(sortie))


def demande_fournisseur(c: Consultation, fournisseur, lignes_ids: list[str], contact=None, echeance: str | None = None,
                        instructions: str | None = None, plages: list[tuple[int, int]] | None = None,
                        cctp: str | None = None, auteur: str = "?") -> DemandeCotation:
    """Brouillon de demande pour UN fournisseur choisi par SIE, sur les seules lignes cochées."""
    from datetime import datetime

    qs = [q for q in c.quantites if q.id in set(lignes_ids)]
    lots = sorted({q.lot for q in qs if q.lot})
    familles = sorted({q.famille or "Divers" for q in qs})
    d = DemandeCotation(lot=", ".join(lots) or None, famille=", ".join(familles) or "Divers", echeance_retour=echeance,
                        fournisseur_id=fournisseur.id, fournisseurs=[fournisseur.raison_sociale],
                        contact=contact.libelle if contact else None,
                        email=(contact.email if contact and contact.email else fournisseur.email) or None,
                        instructions=instructions or None, plages_cctp=plages or [], cctp_fichier=cctp,
                        pieces_utiles=[cctp] if cctp else [])
    for q in qs:
        d.lignes.append(LigneCotation(ligne_quantite_id=q.id, designation=(f"{q.code} " if q.code else "") + q.designation,
                                      unite=q.unite, quantite=q.quantite,
                                      caracteristiques=_caracteristiques(q.designation, c.exigences, q.lot)))
    d.historique.append(f"{datetime.now():%d/%m/%Y %H:%M} brouillon créé par {auteur}")
    return d


def courriel_fournisseur(c: Consultation, d: DemandeCotation) -> str:
    objet = c.fiche.objet.valeur or c.fiche.intitule.valeur or "consultation en cours"
    lignes = "\n".join(f"- {l.designation} : {l.quantite:g} {l.unite or ''}".rstrip() if l.quantite is not None
                       else f"- {l.designation} : quantité à préciser" for l in d.lignes)
    pages = ", ".join(f"{a}-{b}" if a != b else str(a) for a, b in d.plages_cctp)
    pj = (f"Vous trouverez en pièce jointe l'extrait du CCTP utile (pages {pages} du document PDF) et le tableau des quantités."
          if d.plages_cctp else "Vous trouverez en pièce jointe le tableau des quantités.")
    return (f"À : {d.email or '[adresse à vérifier]'}\n"
            f"Objet : Demande de prix - {objet} - {d.famille}\n\n"
            f"{'Bonjour ' + d.contact if d.contact else 'Madame, Monsieur'},\n\n"
            f"Dans le cadre de notre réponse à la consultation « {objet} », nous vous remercions de nous adresser "
            f"votre offre de prix pour les fournitures et prestations suivantes :\n\n{lignes}\n\n{pj}\n"
            + (f"\n{d.instructions}\n" if d.instructions else "")
            + f"\nMerci de nous retourner votre offre avant le {d.echeance_retour or '[date à fixer]'}.\n\n"
              f"Cordialement,\n[Nom, SIE]\n\n[BROUILLON : destinataires et pièces jointes à vérifier par SIE avant envoi]")


def exporter_demande(c: Consultation, d: DemandeCotation, chemin: str | Path) -> Path:
    """Bordereau de consultation Excel d'une demande (quantités, colonnes de prix à remplir, brouillon de courriel)."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Demande de prix"
    ws.append([f"Demande de prix - {d.fournisseurs[0] if d.fournisseurs else ''} - {d.famille}"])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([f"Consultation : {c.fiche.objet.valeur or c.fiche.intitule.valeur or c.id}"])
    ws.append([f"Retour souhaité avant le : {d.echeance_retour or 'à fixer par SIE'}"])
    ws.append([])
    ws.append(["Désignation", "Unité", "Quantité", "Caractéristiques (extraits du CCTP)", "Prix unitaire HT", "Délai", "Observations"])
    for cell in ws[5]:
        cell.font = Font(bold=True)
    for l in d.lignes:
        ws.append([l.designation, l.unite, l.quantite, "\n".join(l.caracteristiques), None, None, None])
    for col, w in zip("ABCDEFG", (45, 8, 10, 70, 16, 12, 30)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=6):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws2 = wb.create_sheet("Courriel (brouillon)")
    ws2["A1"] = courriel_fournisseur(c, d)
    ws2["A1"].alignment = Alignment(wrap_text=True, vertical="top")
    ws2.column_dimensions["A"].width = 110
    ws2.row_dimensions[1].height = 400
    chemin = Path(chemin)
    wb.save(chemin)
    return chemin
