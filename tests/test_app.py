"""Chaque page de l'interface s'affiche sans erreur sur une affaire fictive."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

PAGES = ["tableau", "consultations", "chargement", "analyse", "fiche", "metres", "demandes", "dpgf", "memoire", "documents",
         "controle", "parametres"]


@pytest.fixture(scope="module")
def environnement(tmp_path_factory):
    from generer_donnees_fictives import generer

    from sie_tender.affaire import EspaceTravail
    from sie_tender.f3_quantites import importer_quantites
    from sie_tender.f6_archives import Bibliotheque
    from sie_tender.models import CitationIA, ReponseIA
    from sie_tender.pipeline import analyser_affaire

    d = tmp_path_factory.mktemp("app")
    generer(d / "exemples")
    Bibliotheque.indexer(d / "exemples" / "archives_fictives").sauver(d / "index.json")
    a = EspaceTravail(d / "affaires").creer()
    for f in (d / "exemples" / "dce_fictif_valbourg").iterdir():
        a.deposer("dce", f.name, f.read_bytes())
    analyser_affaire(a, Bibliotheque.charger(d / "index.json"))
    a.c.fiche.decision = "GO"
    a.c.lots[1].retenu = True
    a.c.quantites, _ = importer_quantites(d / "exemples" / "chiffrage_fictif" / "metre_lot02.xlsx")
    a.c.reponses_ia.append(ReponseIA(type="question", question="Date ?", texte="27 novembre", moteur="test",
                                     citations=[CitationIA(fichier="01_RC_Valbourg.pdf", page=2, extrait="x", verifiee=False)]))
    # État avancé : registre, pièces, référentiels, demande, devis, choix manuel, mémoire par lot
    from sie_tender.f4_cotations import demande_fournisseur
    from sie_tender.f5_dpgf import associer, construire_dpgf, importer_devis, offres_par_poste, retenir
    from sie_tender.f6_archives import preparer_memoires
    from sie_tender.referentiels import Contact, Fournisseur, PersonneVisite, Referentiels, Utilisateur
    from sie_tender.suivi import initialiser_registre, synchroniser_pieces
    c = a.c
    initialiser_registre(c)
    c.registre[0].statut = "Bloquant"
    synchroniser_pieces(c)
    r = Referentiels(fournisseurs=[Fournisseur(raison_sociale="Distribution Ouest (fictif)", specialites=["Éclairage"],
                                               contacts=[Contact(nom="Martin", email="martin@exemple-fictif.fr")])],
                     utilisateurs=[Utilisateur(nom="Durand", prenom="Alex", role="Direction")],
                     visites=[PersonneVisite(nom="Petit", prenom="Sam")])
    r.sauver(d / "affaires" / "_referentiels.json")
    c.demandes_cotation.append(demande_fournisseur(c, r.fournisseurs[0], [q.id for q in c.quantites[:3]], r.fournisseurs[0].contacts[0]))
    for f in ("devis_elec_distribution_ouest.xlsx", "devis_lumiere_plus.pdf"):
        dv = importer_devis(d / "exemples" / "chiffrage_fictif" / f, lot="2")
        associer(c, dv)
        c.devis.append(dv)
    qid, lst = next(iter(offres_par_poste(c).items()))
    retenir(c, qid, lst[0][0].id, "test", "test")
    c.dpgf = construire_dpgf(c)
    preparer_memoires(c, "par_lot", ["2"])
    a.sauver()
    return d, a.id


@pytest.mark.parametrize("page", PAGES)
def test_page(environnement, monkeypatch, page):
    d, aid = environnement
    for k, v in {"SIE_DATA": str(d), "SIE_ESPACE": str(d / "affaires"), "SIE_INDEX": str(d / "index.json"),
                 "SIE_EXEMPLES": "1", "SIE_LLM": "aucun"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SIE_MOT_DE_PASSE", raising=False)
    at = AppTest.from_file(str(RACINE / "app.py"), default_timeout=120)
    at.session_state["affaire"] = aid
    at.session_state["_page_test"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.mark.parametrize("role,bloque", [("Bureau d'études", True), ("Direction", False)])
def test_decision_reservee_a_la_direction(environnement, monkeypatch, role, bloque):
    d, aid = environnement
    for k, v in {"SIE_DATA": str(d), "SIE_ESPACE": str(d / "affaires"), "SIE_INDEX": str(d / "index.json"), "SIE_LLM": "aucun"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SIE_MOT_DE_PASSE", raising=False)
    at = AppTest.from_file(str(RACINE / "app.py"), default_timeout=120)
    at.session_state["affaire"] = aid
    at.session_state["_page_test"] = "fiche"
    at.session_state["role"] = role
    at.run()
    assert not at.exception
    assert next(r for r in at.radio if r.label == "Décision").disabled is bloque


def test_fiche_go_puis_lots(environnement, monkeypatch):
    """La direction enregistre GO ; la décision est historisée et les lots deviennent cochables."""
    d, aid = environnement
    for k, v in {"SIE_DATA": str(d), "SIE_ESPACE": str(d / "affaires"), "SIE_INDEX": str(d / "index.json"), "SIE_LLM": "aucun"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SIE_MOT_DE_PASSE", raising=False)
    from sie_tender.affaire import EspaceTravail
    esp = EspaceTravail(d / "affaires")
    a = esp.ouvrir(aid)
    a.c.fiche.decision = None
    a.sauver()
    at = AppTest.from_file(str(RACINE / "app.py"), default_timeout=120)
    at.session_state["affaire"] = aid
    at.session_state["_page_test"] = "fiche"
    at.session_state["role"] = "Direction"
    at.session_state["utilisateur"] = "Alex Durand"
    at.run()
    next(r for r in at.radio if r.label == "Décision").set_value("GO")
    next(b for b in at.button if b.label == "Enregistrer la fiche").click()
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    c = esp.ouvrir(aid).c
    assert c.fiche.decision == "GO" and c.fiche.decideur == "Alex Durand"
    assert c.fiche.historique_decisions and "GO par Alex Durand" in c.fiche.historique_decisions[-1]


def test_sans_consultation(environnement, monkeypatch):
    d, _ = environnement
    for k, v in {"SIE_DATA": str(d), "SIE_ESPACE": str(d / "affaires"), "SIE_INDEX": str(d / "index.json"), "SIE_LLM": "aucun"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("SIE_MOT_DE_PASSE", raising=False)
    for page in ("tableau", "fiche", "parametres"):
        at = AppTest.from_file(str(RACINE / "app.py"), default_timeout=120)
        at.session_state["_page_test"] = page
        at.run()
        assert not at.exception, (page, [e.value for e in at.exception])
