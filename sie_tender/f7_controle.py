"""F7 : contrôle final et export du dossier pour validation humaine.

Le dossier exporté est destiné à la relecture et au dépôt manuel par SIE :
rien n'est envoyé à la plateforme de l'acheteur.
"""
from __future__ import annotations

import re
import zipfile
from datetime import date
from pathlib import Path

from .affaire import Affaire
from .export import exporter_excel, exporter_trame_docx, sections_memoire
from .f2_exigences import PIECES_CONNUES
from .f5_dpgf import controles_dpgf, exporter_dpgf
from .models import CategorieExigence, Constat, Statut, StatutSection
from .texte import normaliser

MOTS_RSE = {
    "insertion": r"insertion|clause sociale",
    "dechets": r"dechet|tri\b|bordereau",
    "nuisances": r"nuisance|bruit|poussiere",
    "consommations": r"consommation|energie|economie",
    "deplacements": r"deplacement|covoiturage|transport",
    "environnement": r"environnement|carbone|reemploi|recycl",
}


# Noms de fichiers courants, plus courts que les intitulés des RC
FICHIERS = [
    (r"\bref(?:erence)?s?\b", "Références de travaux similaires", "candidature"),
    (r"acte (?:d )?engagement|\bae\b|\batte\b", "Acte d'engagement", "offre"),
    (r"visite", "Attestation de visite", "offre"),
    (r"assurance|decennale|\brc pro\b", "Attestations d'assurance (RC, décennale)", "candidature"),
    (r"planning", "Planning prévisionnel", "offre"),
]


def _classer_fichier(nom: str) -> tuple[str | None, str]:
    """Rattache un fichier déposé à une pièce attendue d'après son nom."""
    n = normaliser(Path(nom).stem.replace("_", " ").replace("-", " ")).replace("'", " ")
    for motif, intitule, etape in FICHIERS:
        if re.search(motif, n):
            return intitule, etape
    for motif, intitule, etape in PIECES_CONNUES:
        if re.search(motif, n):
            return intitule, etape
    return None, "annexe"


def controler(a: Affaire, aujourdhui: date | None = None) -> list[Constat]:
    c = a.c
    aujourdhui = aujourdhui or date.today()
    out: list[Constat] = []

    def ajout(g, objet, msg):
        out.append(Constat(gravite=g, objet=objet, message=msg))

    # Décision et lots
    if c.fiche.decision != "GO":
        ajout("bloquant", "Décision", "Décision GO non enregistrée par la direction.")
    if not any(l.retenu for l in c.lots):
        ajout("bloquant", "Lots", "Aucun lot retenu.")
    if c.fiche.visite_obligatoire.valeur == "oui" and not c.fiche.responsable_visite:
        ajout("a_verifier", "Visite", "Visite obligatoire : collaborateur chargé de la visite non désigné.")
    for nom, ch in c.fiche.champs().items():
        if ch.statut == Statut.A_VERIFIER and nom in ("date_limite_remise", "reference", "visite_obligatoire"):
            ajout("a_verifier", "Fiche", f"Champ « {nom} » non confirmé.")
    limite = None
    from .f4_cotations import lire_date
    limite = lire_date(c.fiche.date_limite_remise.valeur)
    if limite and limite < aujourdhui:
        ajout("bloquant", "Délai", f"Date limite de remise dépassée ({limite:%d/%m/%Y}).")

    # Pièces à fournir
    deposes = {}
    for f in sorted(a.dossier("pieces_reponse").iterdir()):
        intitule, _ = _classer_fichier(f.name)
        if intitule:
            deposes.setdefault(intitule, []).append(f.name)
    produits = {"DPGF": bool(c.dpgf), "Mémoire technique": bool(c.trame_memoire)}
    if c.pieces_reponse:  # rubrique Documents renseignée : elle fait foi
        for pr in c.pieces_reponse:
            nom = pr.intitule + (f" (lot {pr.lot})" if pr.lot and f"lot {pr.lot}" not in pr.intitule else "")
            produite = next((v for k, v in produits.items() if pr.intitule.startswith(k)), None)
            if produite is not None and not pr.recu:
                if produite:
                    ajout("a_verifier", nom, "Générée par l'outil : à relire, puis à marquer reçue et validée dans Documents.")
                else:
                    ajout("bloquant", nom, "Pièce à produire avec l'outil, non encore générée.")
                continue
            if not pr.recu:
                ajout("bloquant" if pr.obligatoire == "Obligatoire" else "a_verifier", nom,
                      "Pièce non reçue." if pr.obligatoire == "Obligatoire" else "Pièce non reçue (caractère obligatoire à confirmer dans le RC).")
            elif not pr.valide:
                ajout("a_verifier", nom, "Pièce reçue, non validée par SIE (signature, format, date).")
    for pf in ([] if c.pieces_reponse else c.pieces_a_fournir):
        if pf.intitule in produits:
            if not produits[pf.intitule]:
                ajout("bloquant", pf.intitule, "Pièce à produire avec l'outil, non encore générée.")
            continue
        if pf.intitule in deposes:
            continue
        if pf.document_propose:
            if pf.validite and pf.validite < aujourdhui:
                ajout("bloquant", pf.intitule, f"Document proposé ({pf.document_propose}) périmé depuis le {pf.validite:%d/%m/%Y}.")
            else:
                ajout("a_verifier", pf.intitule, f"Non déposé ; document d'archive proposé : {pf.document_propose}.")
        else:
            ajout("bloquant", pf.intitule, "Pièce manquante.")
    if (c.fiche.visite_obligatoire.valeur == "oui" and "Attestation de visite" not in deposes
            and not any("visite" in pr.intitule.lower() and pr.recu for pr in c.pieces_reponse)
            and not any(pf.intitule == "Attestation de visite" for pf in c.pieces_a_fournir)):
        ajout("bloquant", "Attestation de visite", "Visite obligatoire : attestation non déposée.")

    # Chiffrage
    if not c.quantites:
        ajout("bloquant", "Quantités", "Aucun métré importé.")
    elif any(q.statut == Statut.A_VERIFIER for q in c.quantites):
        ajout("a_verifier", "Quantités", f"{sum(q.statut == Statut.A_VERIFIER for q in c.quantites)} lignes de métré à vérifier.")
    sans_prix = [p for p in c.dpgf if p.prix_unitaire_vente is None]
    if c.dpgf and sans_prix:
        ajout("bloquant", "DPGF", f"{len(sans_prix)} postes sans prix de vente.")
    out += [x for x in controles_dpgf(c) if x.gravite != "info"]

    # Mémoire
    sections = sections_memoire(c)
    a_completer = [s.titre + (f" (lot {s.lot})" if s.lot else "") for s in sections if s.statut == StatutSection.A_COMPLETER and not s.contenu]
    if a_completer:
        ajout("bloquant", "Mémoire", "Sections à compléter : " + " ; ".join(a_completer))
    non_valides = [s.titre for s in sections if s.statut != StatutSection.VALIDE]
    if non_valides and not a_completer:
        ajout("a_verifier", "Mémoire", f"{len(non_valides)} sections non validées par SIE.")
    texte_memoire = normaliser(" ".join((s.contenu or "") + " " + s.titre for s in sections))
    for sous in sorted({e.sous_categorie for e in c.exigences if e.categorie == CategorieExigence.RSE and e.sous_categorie}):
        if sous in MOTS_RSE and not re.search(MOTS_RSE[sous], texte_memoire):
            ajout("a_verifier", "RSE", f"Exigence RSE « {sous} » du DCE non traitée dans le mémoire.")

    # Registre de vigilance : une alerte bloquante reste visible jusqu'ici
    for al in c.registre:
        src = f" ({al.source}{' p. ' + al.page if al.page else ''})" if al.source else ""
        if al.statut == "Bloquant":
            ajout("bloquant", "Vigilance", f"{al.intitule}{src} : alerte bloquante non levée" + (f", responsable {al.responsable}" if al.responsable else "") + ".")
        elif al.memoire and al.statut in ("À vérifier", "À traiter", "En cours"):
            ajout("a_verifier", "Vigilance → mémoire", f"{al.intitule}{src} : point à intégrer au mémoire, non traité.")
    if c.registre and any(al.priorite == "haute" and al.statut in ("À vérifier", "À traiter") for al in c.registre):
        n = sum(al.priorite == "haute" and al.statut in ("À vérifier", "À traiter") for al in c.registre)
        ajout("a_verifier", "Vigilance", f"{n} point(s) prioritaire(s) repéré(s) dans le DCE pas encore vérifié(s).")
    for livrable in c.a_revalider:
        ajout("a_verifier", "Revalidation", f"Donnée amont modifiée : {livrable} à revoir et revalider.")

    # Validations humaines
    for etape in ("fiche", "quantites", "dpgf", "memoire"):
        if not a.est_valide(etape):
            ajout("a_verifier", "Validation", f"Étape « {etape} » non validée.")
    for cle, libelle in VALIDATIONS_FINALES.items():
        if not c.validations_finales.get(cle):
            ajout("a_verifier", "Validation finale", f"{libelle} non approuvée.")
    c.controles = out
    return out


VALIDATIONS_FINALES = {"technique": "Validation technique", "chiffrage": "Validation du chiffrage",
                       "administrative": "Validation administrative"}


def peut_declarer_pret(c, constats: list[Constat]) -> tuple[bool, str]:
    """« Prêt à déposer » : impossible avec une anomalie bloquante, sauf dérogation formalisée."""
    bloquants = [k for k in constats if k.gravite == "bloquant"]
    manquantes = [v for k, v in VALIDATIONS_FINALES.items() if not c.validations_finales.get(k)]
    if manquantes:
        return False, "Validations manquantes : " + ", ".join(manquantes) + "."
    if bloquants and not c.derogation:
        return False, f"{len(bloquants)} anomalie(s) bloquante(s) non levée(s) et pas de dérogation."
    return True, "Dérogation enregistrée : " + c.derogation if bloquants else "Aucune anomalie bloquante."


def exporter_dossier(a: Affaire, modele_dpgf: str | Path | None = None) -> Path:
    """Construit le ZIP du dossier de réponse, avec la liste de contrôle en tête."""
    c = a.c
    exp = a.dossier("exports")
    dpgf = exporter_dpgf(c, exp / "DPGF.xlsx", modele_dpgf)
    memoire = exp / "Memoire_technique.docx"
    exporter_trame_docx(c, memoire)
    analyse = exp / "Analyse_DCE.xlsx"
    exporter_excel(c, analyse)
    constats = controler(a)
    liste = exp / "00_Liste_de_controle.md"
    lignes = ["# Liste de contrôle avant dépôt", "", f"Affaire : {c.fiche.intitule.valeur or c.id}", "",
              "Dossier préparé par l'outil pour relecture. Le dépôt sur la plateforme reste fait par SIE.", "",
              "| Gravité | Objet | Constat |", "| --- | --- | --- |"]
    ordre = {"bloquant": 0, "a_verifier": 1, "info": 2}
    for k in sorted(constats, key=lambda x: ordre.get(x.gravite, 3)):
        lignes.append(f"| {k.gravite} | {k.objet} | {k.message} |")
    if not constats:
        lignes.append("| info | Dossier | Aucun manque détecté. |")
    liste.write_text("\n".join(lignes), encoding="utf-8")

    zip_path = exp / f"Dossier_reponse_{c.id}.zip"
    retenus = [l.numero for l in c.lots if l.retenu]
    plusieurs = len(retenus) > 1
    memoires = {}
    if c.mode_memoire == "par_lot":
        for lot in c.lots_memoire:
            m = exp / f"Memoire_technique_lot{lot}.docx"
            exporter_trame_docx(c, m, lot)
            memoires[lot] = m
    rattaches = {pr.fichier: pr for pr in c.pieces_reponse if pr.fichier}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(liste, "00_Liste_de_controle.md")
        for f in sorted(a.dossier("pieces_reponse").iterdir()):
            pr = rattaches.get(f.name)
            if pr:
                etape, lot = pr.etape, pr.lot
            else:
                _, etape = _classer_fichier(f.name)
                lot = None
            rep = {"candidature": "01_Candidature", "offre": "02_Offre"}.get(etape, "03_Annexes")
            if rep == "02_Offre" and lot and plusieurs:
                rep += f"/Lot_{lot}"
            z.write(f, f"{rep}/{f.name}")
        z.write(dpgf, "02_Offre/DPGF.xlsx")
        if memoires:
            for lot, m in memoires.items():
                z.write(m, f"02_Offre/Lot_{lot}/{m.name}")
        else:
            z.write(memoire, "02_Offre/Memoire_technique.docx")
        z.write(analyse, "04_Travail_interne_ne_pas_deposer/Analyse_DCE.xlsx")
    return zip_path
