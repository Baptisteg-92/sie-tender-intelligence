"""Fonctions ajoutées pour suivre la maquette V2 et les spécifications détaillées SIE (10/11/2026)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "scripts"))

from generer_donnees_fictives import generer  # noqa: E402

from sie_tender.affaire import EspaceTravail  # noqa: E402
from sie_tender.export import sections_memoire  # noqa: E402
from sie_tender.f3_quantites import apercu, controler_metre, importer_quantites  # noqa: E402
from sie_tender.f4_cotations import courriel_fournisseur, demande_fournisseur, extraire_pages, lire_plages  # noqa: E402
from sie_tender.f5_dpgf import associer, construire_dpgf, importer_devis, offres_par_poste, retenir  # noqa: E402
from sie_tender.f6_archives import Bibliotheque, preparer_memoires  # noqa: E402
from sie_tender.f7_controle import controler, peut_declarer_pret  # noqa: E402
from sie_tender.pipeline import analyser_affaire  # noqa: E402
from sie_tender.referentiels import ConfigBibliotheque, Contact, Fournisseur, Referentiels, email_valide  # noqa: E402
from sie_tender.suivi import initialiser_registre, signaler_modification, statut_metier, synchroniser_pieces  # noqa: E402
from sie_tender.synchro import synchroniser  # noqa: E402


@pytest.fixture(scope="module")
def donnees(tmp_path_factory):
    d = tmp_path_factory.mktemp("v2")
    generer(d)
    return d


@pytest.fixture()
def affaire(donnees, tmp_path):
    a = EspaceTravail(tmp_path / "affaires").creer()
    for f in (donnees / "dce_fictif_valbourg").iterdir():
        a.deposer("dce", f.name, f.read_bytes())
    analyser_affaire(a, Bibliotheque.indexer(donnees / "archives_fictives"))
    return a


def test_fiche_champs_du_modele_sie(affaire):
    f = affaire.c.fiche
    assert f.coordonnateur_sps.valeur and f.coordonnateur_sps.source  # lu dans le RC, avec sa source
    assert f.architecte.valeur is None and f.architecte.statut.value == "a_verifier"  # absent : à vérifier, pas inventé


def test_registre_source_et_sans_doublon(affaire):
    c = affaire.c
    n = initialiser_registre(c)
    assert n >= 12
    dce = [x for x in c.registre if x.origine == "dce"]
    assert dce and all(x.source for x in dce)
    assert any(x.priorite == "haute" for x in dce)
    c.registre[0].statut = "Traité"
    assert initialiser_registre(c) == 0  # deuxième passage : rien d'ajouté, rien d'écrasé
    assert c.registre[0].statut == "Traité"


def test_alerte_bloquante_visible_au_controle(affaire):
    c = affaire.c
    initialiser_registre(c)
    c.registre[0].statut = "Bloquant"
    assert any(k.objet == "Vigilance" and k.gravite == "bloquant" for k in controler(affaire))


def test_metre_csv_mappage_et_doublons(tmp_path):
    csv = tmp_path / "metre.csv"
    csv.write_text("Réf;Libellé article;U;Qté\nA1;Luminaire LED;u;12\nA1;Luminaire LED;u;12\nA2;Câble;rouleau;3\nA3;Prise;u;\n",
                   encoding="utf-8")
    entetes, detecte, ligne = apercu(csv)
    assert entetes[1] == "Libellé article" and detecte.get("designation") == 1
    lignes, _ = importer_quantites(csv, "2", colonnes={"code": 0, "designation": 1, "unite": 2, "quantite": 3}, ligne_entete=ligne)
    ctl = controler_metre(lignes)
    assert len(lignes) == 4 and ctl["doublons"] == 1 and ctl["unites"] == 1 and ctl["quantites"] == 1
    assert all(l.lot == "2" and l.source.fichier == "metre.csv" for l in lignes)


def test_extrait_cctp_plages_non_contigues(donnees, tmp_path):
    from reportlab.pdfgen import canvas

    from sie_tender.pdf import lire_pdf
    pdf = tmp_path / "cctp.pdf"
    cv = canvas.Canvas(str(pdf))
    for i in range(1, 21):
        cv.drawString(72, 720, f"Page {i}")
        cv.showPage()
    cv.save()
    plages, erreurs = lire_plages("3-4, 10, 18-19", 20)
    assert not erreurs and plages == [(3, 4), (10, 10), (18, 19)]
    assert lire_plages("5-2", 20)[1] and lire_plages("30", 20)[1]
    sortie = extraire_pages(pdf, plages, tmp_path / "extrait.pdf")
    assert [t.strip() for _, t, _ in lire_pdf(sortie)[0]] == ["Page 3", "Page 4", "Page 10", "Page 18", "Page 19"]


def test_demande_ciblee_par_fournisseur(affaire, donnees):
    c = affaire.c
    c.quantites, _ = importer_quantites(donnees / "chiffrage_fictif" / "metre_lot02.xlsx")
    f = Fournisseur(raison_sociale="Lumière fictive", contacts=[Contact(prenom="Sam", nom="Petit", email="sam@exemple-fictif.fr")])
    d = demande_fournisseur(c, f, [c.quantites[0].id, c.quantites[2].id], f.contacts[0], "10/11/2026", plages=[(2, 3)], cctp="cctp.pdf")
    assert len(d.lignes) == 2 and d.email == "sam@exemple-fictif.fr" and d.statut == "brouillon"
    mail = courriel_fournisseur(c, d)
    assert "Bonjour Sam Petit" in mail and "pages 2-3" in mail and "BROUILLON" in mail


def test_aucun_choix_automatique_du_moins_disant(affaire, donnees):
    c = affaire.c
    c.quantites, _ = importer_quantites(donnees / "chiffrage_fictif" / "metre_lot02.xlsx")
    for f in ("devis_elec_distribution_ouest.xlsx", "devis_lumiere_plus.pdf"):
        dv = importer_devis(donnees / "chiffrage_fictif" / f, lot="2")
        associer(c, dv)
        c.devis.append(dv)
    c.dpgf = construire_dpgf(c)
    assert all(p.prix_materiel is None for p in c.dpgf)
    qid, lst = next((k, v) for k, v in offres_par_poste(c).items() if len(v) >= 2)
    plus_cher = max(lst, key=lambda x: x[1].prix_unitaire)
    retenir(c, qid, plus_cher[0].id, "délai plus court", "test")  # SIE peut retenir une offre plus chère
    c.dpgf = construire_dpgf(c)
    p = next(p for p in c.dpgf if p.ligne_quantite_id == qid)
    assert p.prix_materiel == plus_cher[1].prix_unitaire and p.fournisseur == plus_cher[0].fournisseur


def test_memoire_commun_ou_par_lot(affaire):
    c = affaire.c
    base = len(c.trame_memoire)
    preparer_memoires(c, "par_lot", ["2", "4"])
    assert len(sections_memoire(c, "2")) == base and len(sections_memoire(c, "4")) == base
    s2 = sections_memoire(c, "2")[0]
    s2.contenu = "Texte propre au lot 2."
    assert sections_memoire(c, "4")[0].contenu is None  # mémoires indépendants
    preparer_memoires(c, "commun", ["2", "4"])
    titres = [s.titre for s in sections_memoire(c)]
    assert any("lot 2" in t for t in titres) and any("lot 4" in t for t in titres)
    assert any(s.contenu == "Texte propre au lot 2." for s in c.trame_memoire)  # rien de rédigé n'est perdu


def test_pret_a_deposer_bloque_sans_derogation(affaire):
    c = affaire.c
    synchroniser_pieces(c)
    constats = controler(affaire)
    assert any(k.gravite == "bloquant" for k in constats)
    c.validations_finales = {"technique": "x", "chiffrage": "x", "administrative": "x"}
    ok, _ = peut_declarer_pret(c, constats)
    assert not ok
    c.derogation = "Pièce transmise par courrier séparé (par Direction)"
    ok, raison = peut_declarer_pret(c, constats)
    assert ok and "Dérogation" in raison


def test_revalidation_apres_modification_amont(affaire, donnees):
    c = affaire.c
    c.quantites, _ = importer_quantites(donnees / "chiffrage_fictif" / "metre_lot02.xlsx")
    c.dpgf = construire_dpgf(c)
    signaler_modification(c, "quantites")
    assert "DPGF" in c.a_revalider
    assert any(k.objet == "Revalidation" for k in controler(affaire))


def test_consultations_isolees(donnees, tmp_path):
    esp = EspaceTravail(tmp_path / "affaires")
    a, b = esp.creer(), esp.creer()
    a.c.quantites, _ = importer_quantites(donnees / "chiffrage_fictif" / "metre_lot02.xlsx")
    a.c.registre = []
    initialiser_registre(a.c)
    a.sauver()
    b2 = esp.ouvrir(b.id)
    assert not b2.c.quantites and not b2.c.registre and not b2.c.devis
    assert statut_metier(b2.c) == "DCE à charger"
    assert {x for x, _ in esp.lister()} == {a.id, b.id}  # le fichier des référentiels n'est pas une affaire


def test_referentiels_et_synchro(donnees, tmp_path):
    chemin = tmp_path / "affaires" / "_referentiels.json"
    r = Referentiels(fournisseurs=[Fournisseur(raison_sociale="A (fictif)", specialites=["CVC"]),
                                   Fournisseur(raison_sociale="B (fictif)", specialites=["Éclairage"])])
    r.sauver(chemin)
    r2 = Referentiels.charger(chemin)
    assert [f.raison_sociale for f in r2.fournisseurs_pour("Éclairage")][0] == "B (fictif)"
    assert email_valide("a@b.fr") and not email_valide("a@b")

    cfg = ConfigBibliotheque(chemin=str(donnees / "archives_fictives"), valides_seulement=False)
    index = tmp_path / "index.json"
    premier = synchroniser(cfg, index)
    assert "ajout" in premier and index.exists() and cfg.nb_fichiers > 0
    assert synchroniser(cfg, index).startswith("Aucun changement")
    assert synchroniser(ConfigBibliotheque(chemin=str(tmp_path / "absent")), index).startswith("Dossier introuvable")
