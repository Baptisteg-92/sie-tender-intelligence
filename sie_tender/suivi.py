"""Suivi transversal d'une consultation : registre de vigilance (F2), pièces de réponse,
statut métier, livrables à revalider après une modification amont.

Le registre part d'une liste de contrôle fixe (points qu'on oublie souvent) et des
points repérés dans le DCE avec leur source. Rien n'y est marqué « traité » par l'outil.
"""
from __future__ import annotations

import re

from .export import ref
from .ingestion import piece_generique
from .models import Alerte, CategorieExigence, Consultation, PieceReponse, Statut, TypePiece

# (intitulé, catégorie, destination, à intégrer au mémoire)
CHECKLIST = [
    ("Date et heure limites de remise de l'offre", "Administratif", "Pièces administratives", False),
    ("Visite obligatoire, attestation signée et rendez-vous", "Administratif", "Pièces administratives", False),
    ("Questions, demandes de précisions et date limite", "Administratif", "Direction", False),
    ("Pièces exigées, signatures, formats et variantes autorisées", "Administratif", "Contrôle final", False),
    ("Qualifications, habilitations et certifications demandées", "Technique", "Mémoire technique", True),
    ("Exigences techniques, marques, équivalences et performances", "Technique", "Mémoire technique", True),
    ("Interfaces entre lots, limites de prestations et réservations", "Technique", "Mémoire technique", True),
    ("Phasage, délais, accès, site occupé et coactivité", "Technique", "Mémoire technique", True),
    ("Essais, autocontrôles, DOE, réception et garanties", "Technique", "Mémoire technique", True),
    ("Sécurité chantier, SPS, déchets et nuisances", "Environnement / sécurité", "Mémoire technique", True),
    ("Cohérence quantités, DPGF, options, PSE et prix", "Économique", "DPGF", False),
    ("Pénalités, assurances, retenues et obligations contractuelles", "Contractuel", "Direction", False),
]

_DEST_PAR_CATEGORIE = {
    CategorieExigence.ELIMINATOIRE: ("Administratif", "Contrôle final", False),
    CategorieExigence.RSE: ("Environnement / sécurité", "Mémoire technique", True),
    CategorieExigence.SITE_OCCUPE: ("Technique", "Mémoire technique", True),
    CategorieExigence.PENALITE: ("Contractuel", "Direction", False),
    CategorieExigence.INTERFACE: ("Technique", "Mémoire technique", True),
}

OUVERTS = ("À vérifier", "À traiter", "En cours", "Bloquant")


def initialiser_registre(c: Consultation) -> int:
    """Ajoute au registre la liste de contrôle et les points sourcés du DCE qui n'y sont pas encore.

    Ne modifie jamais une ligne existante (statut, action, responsable restent ceux saisis par SIE).
    Renvoie le nombre de lignes ajoutées.
    """
    connus = {(a.intitule, a.source, a.page) for a in c.registre}
    ajouts: list[Alerte] = []

    def ajouter(a: Alerte) -> None:
        cle = (a.intitule, a.source, a.page)
        if cle not in connus:
            connus.add(cle)
            ajouts.append(a)

    # Alertes de la fiche : « Libellé (fichier p.N) : « extrait » »
    for texte in c.fiche.alertes:
        m = re.match(r"(?P<lib>[^(]+)\((?P<fic>.+?) p\.(?P<page>\w+)\) : « (?P<ext>.*)$", texte)
        if not m:
            continue
        lib = m.group("lib").strip()
        ajouter(Alerte(intitule=lib, categorie="Administratif", destination="Contrôle final", source=m.group("fic"),
                       page=m.group("page"), extrait=m.group("ext").rstrip("» ").strip(), origine="dce",
                       priorite="haute" if re.search(r"obligatoire|irrecevab|rejet|exig", lib, re.I) else "normale"))
    # Points du registre : pièces écrites pour ce marché (RC, CCAP, CCTP, AE, cadre de réponse),
    # pas les diagnostics et annexes, ni les notices génériques ; les interfaces restent dans le mémoire.
    sources_registre = {p.fichier for p in c.pieces if p.type not in (TypePiece.ANNEXE, TypePiece.AUTRE)
                        and not piece_generique(p.fichier)}
    textes_vus: set[str] = set()
    sources_admin = {p.fichier for p in c.pieces if p.type in (TypePiece.RC, TypePiece.CCAP, TypePiece.AE)}
    for e in c.exigences:
        if e.categorie not in _DEST_PAR_CATEGORIE or e.categorie == CategorieExigence.INTERFACE:
            continue
        if e.source and e.source.fichier not in sources_registre and not re.search(r"cadre de r[ée]ponse", e.source.fichier, re.I):
            continue
        # Environnement / RSE : les CCTP en sont pleins (déchets, amiante...) ; dans le registre on garde ce que
        # demandent le RC, le CCAP et le cadre de réponse, le reste sert à rédiger le mémoire.
        if e.categorie == CategorieExigence.RSE and e.source and e.source.fichier not in sources_admin \
                and not re.search(r"cadre de r[ée]ponse", e.source.fichier, re.I):
            continue
        # Références réglementaires citées (« Décret n°... », « L'arrêté du... ») et répétitions : pas des points à traiter.
        if re.match(r"\W*(?:l[ae']\s*)?(?:d[ée]cret|arr[êe]t[ée]|loi|norme|circulaire|code|directive|r[eè]glement \(|nf\s|dtu)\b", e.texte, re.I):
            continue
        cle_texte = re.sub(r"\W+", " ", e.texte.lower())[:90]
        if cle_texte in textes_vus:
            continue
        textes_vus.add(cle_texte)
        cat, dest, memo = _DEST_PAR_CATEGORIE[e.categorie]
        ajouter(Alerte(intitule=e.texte[:160], categorie=cat, destination=dest, memoire=memo, lot=e.lot,
                       source=e.source.fichier, page=str(e.source.page) if e.source.page else None, extrait=e.source.extrait,
                       priorite="haute" if e.categorie == CategorieExigence.ELIMINATOIRE else "normale", origine="dce"))
    for intitule, cat, dest, memo in CHECKLIST:  # liste de contrôle après les points sourcés du DCE
        ajouter(Alerte(intitule=intitule, categorie=cat, destination=dest, memoire=memo, origine="checklist"))
    c.registre += ajouts
    return len(ajouts)


def synthese_registre(c: Consultation) -> dict:
    ouverts = [a for a in c.registre if a.statut in OUVERTS]
    return {"ouverts": len(ouverts), "bloquants": sum(a.statut == "Bloquant" for a in c.registre),
            "memoire": sum(a.memoire for a in c.registre), "memoire_ouverts": sum(a.memoire and a.statut in OUVERTS for a in c.registre),
            "traites": sum(a.statut in ("Traité", "Sans objet") for a in c.registre), "total": len(c.registre)}


def points_memoire(c: Consultation) -> list[Alerte]:
    """Points cochés « à intégrer au mémoire » : ils doivent être traités ou justifiés dans le mémoire."""
    return [a for a in c.registre if a.memoire and a.statut != "Sans objet"]


# --- Pièces de réponse (rubrique Documents) ------------------------------------------------
PRODUITES = [("DPGF", "offre", "Obligatoire"), ("Mémoire technique", "offre", "Obligatoire")]


def synchroniser_pieces(c: Consultation) -> int:
    """Crée la liste des pièces de réponse depuis les pièces exigées par le RC (F2), sans toucher aux états saisis."""
    connues = {(p.intitule, p.lot) for p in c.pieces_reponse}
    n = 0
    for pf in c.pieces_a_fournir:
        if (pf.intitule, None) in connues:
            continue
        c.pieces_reponse.append(PieceReponse(intitule=pf.intitule, etape=pf.etape,
                                             obligatoire="Obligatoire" if pf.obligatoire and pf.statut == Statut.EXTRAIT else "À vérifier",
                                             source=ref(pf.source)))
        connues.add((pf.intitule, None))
        n += 1
    retenus = [l.numero for l in c.lots if l.retenu]
    for intitule, etape, obl in PRODUITES:
        for lot in retenus or [None]:
            nom = intitule if lot is None or len(retenus) == 1 else f"{intitule} du lot {lot}"
            if (nom, lot) not in connues and (intitule, None) not in connues:
                c.pieces_reponse.append(PieceReponse(intitule=nom, etape=etape, lot=lot, obligatoire=obl, source="produite avec l'outil"))
                connues.add((nom, lot))
                n += 1
    if c.fiche.visite_obligatoire.valeur == "oui" and not any("visite" in p.intitule.lower() for p in c.pieces_reponse):
        src = ref(c.fiche.visite_obligatoire.source)
        c.pieces_reponse.append(PieceReponse(intitule="Attestation de visite", etape="candidature", obligatoire="Obligatoire", source=src))
        n += 1
    return n


# --- Statut métier et revalidation ---------------------------------------------------------
def statut_metier(c: Consultation) -> str:
    if c.archivee:
        return "Archivée"
    if c.pret_a_deposer:
        return "Prêt pour dépôt"
    if c.fiche.decision == "NO GO":
        return "NO GO"
    if c.fiche.decision != "GO":
        return "À analyser" if c.pieces else "DCE à charger"
    if c.dpgf or c.devis:
        return "Chiffrage en cours"
    return "En préparation"


def avancement(c: Consultation) -> float:
    etapes = [bool(c.pieces), c.fiche.decision == "GO", bool(c.registre) and synthese_registre(c)["bloquants"] == 0,
              any(v.etape == "quantites" for v in c.validations), bool(c.demandes_cotation), bool(c.selections),
              any(v.etape == "dpgf" for v in c.validations), any(v.etape == "memoire" for v in c.validations),
              bool(c.pieces_reponse) and all(p.valide for p in c.pieces_reponse if p.obligatoire == "Obligatoire"),
              bool(c.pret_a_deposer)]
    return sum(etapes) / len(etapes)


AVAL = {"quantites": ["demandes de prix", "DPGF"], "selections": ["DPGF"], "registre": ["mémoire technique", "contrôle final"],
        "fiche": ["analyse des lots", "mémoire technique"]}


def signaler_modification(c: Consultation, amont: str) -> None:
    """Une donnée amont validée a changé : les livrables aval sont à revoir (exigence d'intégration)."""
    existe = {"demandes de prix": bool(c.demandes_cotation), "DPGF": bool(c.dpgf),
              "mémoire technique": any(s.contenu for s in c.trame_memoire), "contrôle final": bool(c.controles),
              "analyse des lots": any(l.retenu for l in c.lots)}
    for livrable in AVAL.get(amont, []):
        if existe.get(livrable) and livrable not in c.a_revalider:
            c.a_revalider.append(livrable)


def revalide(c: Consultation, livrable: str) -> None:
    c.a_revalider = [x for x in c.a_revalider if x != livrable]
