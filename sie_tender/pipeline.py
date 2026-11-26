"""Enchaînement complet du groupe 1 : DCE -> fiche -> exigences -> trame du mémoire."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from .f1_fiche import construire_fiche
from .f2_exigences import extraire_criteres, extraire_exigences, extraire_pieces_a_fournir
from .f2_synthese import activites_des_lots
from .f6_archives import (Bibliotheque, cadre_reponse, construire_trame, contexte_affaire, proposer_pieces,
                          references_similaires)
from .ingestion import importer_dce, indices_type
from .llm import ClientLLM, client_depuis_env
from .models import Consultation, Piece, TypePiece
from .llm import AucunLLM


CONSIGNE_CLASSEMENT = """Tu classes une pièce d'un dossier de consultation (marché public de travaux).
Types possibles : RC (règlement de la consultation), CCAP (clauses administratives), CCTP (clauses techniques),
AE (acte d'engagement), DPGF (décomposition du prix global et forfaitaire), BPU (bordereau des prix unitaires),
PLAN, ANNEXE, AUTRE. Réponds par {"type": "...", "extrait": "phrase de la page qui justifie, recopiée mot pour mot"}.
Dans le doute, réponds AUTRE."""


def a_confier_a_ia(p: Piece) -> str:
    """Raison d'envoyer la pièce à l'IA pour la classer, ou "" si les règles suffisent.

    Une pièce part à l'IA seulement si elle est lisible, classée par les règles (jamais si SIE l'a corrigée) et :
    1. non reconnue : rien dans le nom ni dans la page de garde (classée AUTRE ou ANNEXE par défaut) ;
    2. contradictoire : le nom dit un type, la page de garde un autre (ex. « Pièce_2_RC.pdf » qui commence par
       « Cahier des clauses administratives »)."""
    if not p.lisible or not p.pages or p.type_methode != "regle":
        return ""
    par_nom, par_contenu = indices_type(p.fichier, p.pages[0].texte)
    if par_nom is None and par_contenu is None:
        return "non reconnue"
    if par_nom and par_contenu and par_nom != par_contenu:
        return f"nom ({par_nom.value}) et page de garde ({par_contenu.value}) en désaccord"
    return ""


def classer_avec_ia(pieces: list[Piece], llm: ClientLLM, journal: list[str]) -> None:
    """Règles d'abord ; l'IA seulement pour les pièces douteuses (voir a_confier_a_ia). On n'envoie que le nom et le début."""
    if isinstance(llm, AucunLLM):
        return
    for p in pieces:
        raison = a_confier_a_ia(p)
        if not raison:
            continue
        debut = "\n".join(pg.texte for pg in p.pages[:2])[:3000]
        rep = llm.json(CONSIGNE_CLASSEMENT, f"Nom du fichier : {p.fichier}\n\nDébut du document :\n{debut}")
        t = str((rep or {}).get("type", "")).upper().strip()
        if t in TypePiece.__members__ and t != "AUTRE":
            p.type, p.type_methode = TypePiece[t], "llm"
            journal.append(f"Import : {p.fichier} classé {t} par l'IA ({raison}).")


def analyser_dce(dce: str | Path, bibliotheque: Bibliotheque | None = None, llm: ClientLLM | None = None,
                 aujourdhui: date | None = None, types_manuels: dict[str, str] | None = None) -> Consultation:
    llm = llm or client_depuis_env()
    c = Consultation(dossier=str(dce))
    c.journal.append(f"Analyse avec LLM : {llm.nom}.")
    c.pieces = importer_dce(dce)
    c.types_manuels = dict(types_manuels or {})
    for p in c.pieces:
        if p.remarque:
            c.journal.append(f"Import : {p.fichier} : {p.remarque}")
        if p.fichier in c.types_manuels and c.types_manuels[p.fichier] in TypePiece.__members__:
            p.type, p.type_methode = TypePiece[c.types_manuels[p.fichier]], "saisie"
    classer_avec_ia(c.pieces, llm, c.journal)

    c.fiche, c.lots = construire_fiche(c.pieces, llm, c.journal)
    activites_des_lots(c.lots, c.pieces, c.fiche.objet.valeur)
    c.exigences = extraire_exigences(c.pieces)
    c.criteres = extraire_criteres(c.pieces)
    if not c.criteres:
        c.journal.append("F2 : critères de notation non trouvés dans le RC, à saisir.")
    elif any(cr.statut.value == "a_verifier" for cr in c.criteres):
        c.journal.append("F2 : la somme des pondérations ne fait pas 100 %, critères à vérifier.")
    c.pieces_a_fournir = extraire_pieces_a_fournir(c.pieces)

    proposer_pieces(c.pieces_a_fournir, bibliotheque, aujourdhui, c.journal)
    cadre = cadre_reponse(c.pieces)
    c.cadre_reponse = cadre[0].source if cadre else None
    if cadre:  # les titres du cadre de réponse ne sont pas des exigences sur les travaux
        pages_cadre = {(ch.source.fichier, ch.source.page) for ch in cadre}
        c.exigences = [e for e in c.exigences if (e.source.fichier, e.source.page) not in pages_cadre
                       or e.categorie.value in ("eliminatoire", "RSE")]
    contexte = contexte_affaire(c.fiche.objet.valeur, c.lots)
    c.trame_memoire = construire_trame(c.criteres, bibliotheque, contexte, c.journal, cadre)
    c.references = references_similaires(bibliotheque, contexte)
    if bibliotheque is None:
        c.journal.append("F6 : pas de bibliothèque d'archives fournie, trame sans passages proposés.")
    return c


def analyser_affaire(affaire, bibliotheque: Bibliotheque | None = None, llm: ClientLLM | None = None) -> None:
    """Analyse le DCE déposé dans une affaire en gardant son identifiant et le travail du groupe 2."""
    r = analyser_dce(affaire.dossier("dce"), bibliotheque, llm, types_manuels=affaire.c.types_manuels)
    c = affaire.c
    for champ in ("pieces", "fiche", "lots", "exigences", "criteres", "pieces_a_fournir", "trame_memoire", "cadre_reponse", "references"):
        setattr(c, champ, getattr(r, champ))
    c.journal = r.journal
    affaire.sauver()
