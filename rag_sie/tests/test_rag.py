"""Tests du moteur RAG sur les archives fictives (aucune donnée SIE, aucun appel extérieur).

    python -m pytest rag_sie/tests -q
    SIE_RAG_TEST_PG=postgresql://sie:sie@localhost/sie_rag python -m pytest rag_sie/tests -q   (avec PostgreSQL)
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_sie.config import ConfigRAG
from rag_sie.decoupage import blocs, lire_docx_titres, sections, titre_de_ligne
from rag_sie.demo import AFFAIRES, JEU_DEMO, generer
from rag_sie.droits import DroitsFichier, Utilisateur, analyser_icacls, autorise
from rag_sie.encodeurs import EncodeurHachage
from rag_sie.entrepot import Filtre
from rag_sie.evaluation import evaluer
from rag_sie.exclusions import envoyable, raison_exclusion
from rag_sie.moteur import Moteur
from rag_sie.redacteurs import (AucunRedacteur, BedrockRedacteur, Citation, ErreurAutorisation, OpenAIRedacteur, Passage,
                                Reponse, _Claude, redacteur)
from rag_sie.redaction import DemandeSection, rediger

EHPAD, GYMNASE, CONFIDENTIEL = AFFAIRES[1]["dossier"], AFFAIRES[2]["dossier"], AFFAIRES[9]["dossier"]
BE = Utilisateur("SIE\\jdupont", ["SIE\\BE"])
DIRECTION = Utilisateur("SIE\\mdurand", ["SIE\\Direction"])


@pytest.fixture(scope="session")
def modele_archives(tmp_path_factory) -> Path:
    return generer(tmp_path_factory.mktemp("modele") / "archives")


def _base(tmp_path: Path) -> str:
    url = os.getenv("SIE_RAG_TEST_PG")
    if not url:
        return str(tmp_path / "donnees" / "rag.sqlite")
    import psycopg
    with psycopg.connect(url, autocommit=True) as cx:
        cx.execute("DROP TABLE IF EXISTS rag_blocs, rag_sections, rag_documents, rag_meta CASCADE")
    return url


@pytest.fixture
def moteur(tmp_path, modele_archives):
    archives = tmp_path / "archives"
    shutil.copytree(modele_archives, archives)
    cfg = ConfigRAG(donnees=tmp_path / "donnees", base=_base(tmp_path), archives=str(archives), droits="fichier")
    m = Moteur(cfg, encodeur=EncodeurHachage(), redacteur=AucunRedacteur())
    cr = m.indexeur.synchroniser()
    assert cr.statut == "termine", cr.message
    yield m
    m.fermer()


# --- découpage ---------------------------------------------------------------------------
def test_titres_et_chemins():
    assert titre_de_ligne("3.2 Habilitations électriques") == (2, "3.2 Habilitations électriques")
    assert titre_de_ligne("CHAPITRE 2 - MOYENS HUMAINS")[0] == 1
    assert titre_de_ligne("## Planning") == (2, "Planning")
    assert titre_de_ligne("2. Les clapets sont remplacés par des modèles motorisés.") is None   # phrase, pas un titre
    assert titre_de_ligne("1.2 Chemins de câbles 300 mm 240 ml") is None                         # ligne de bordereau
    secs = sections([(1, "3 Moyens humains\nNous affectons une équipe.\n3.1 Habilitations\nTous les compagnons sont habilités B2V."),
                     (2, "4 Planning\nLe chantier dure six semaines.")])
    assert [s.chemin for s in secs] == ["3 Moyens humains", "3 Moyens humains > 3.1 Habilitations", "4 Planning"]
    assert secs[2].page == 2


def test_long_paragraphe_coupe_avec_recouvrement():
    secs = sections([(1, "1 Méthodologie\n" + " ".join(f"mot{i}" for i in range(700)))])
    bl = blocs(secs, taille=300, recouvrement=40)
    assert len(bl) == 3 and bl[1].texte.split()[0] == "mot260"
    assert all(b.chemin == "1 Méthodologie" for b in bl)


def test_docx_titres_par_style(modele_archives):
    pages = lire_docx_titres(modele_archives / EHPAD / "Memoire" / "memoire_technique_definitif.docx")
    texte = "\n".join(t for _, t in pages)
    assert "## 2.2 Contraintes du site" in texte


# --- exclusions et droits ----------------------------------------------------------------
def test_exclusions():
    assert raison_exclusion("2022_x/Memoire/CV_Jean_Martin.docx") == "CV"
    assert raison_exclusion("2022_x/RH/bulletin_de_paie_mars.pdf") == "paie ou banque"
    assert raison_exclusion("2022_x/Memoire/memoire_technique.docx") is None
    assert raison_exclusion("2022_x/notes_internes.docx", ["notes internes"]) is not None
    assert not envoyable("2022_x/DPGF_lot1.xlsx", "prix")
    assert not envoyable("2022_x/devis_fournisseur.pdf", "autre")
    assert envoyable("2022_x/memoire.docx", "memoire")


def test_icacls():
    chemin = r"\\srv\memoires\2023\memoire.docx"
    sortie = (f"{chemin} SIE\\BE:(I)(RX)\n                     SIE\\Stagiaires:(DENY)(R)\n"
              "                     BUILTIN\\Administrateurs:(I)(F)\n                     AUTORITE NT\\Système:(I)(F)\n"
              "                     SIE\\Compta:(I)(W)\n\n1 fichiers correctement traités ; échec du traitement de 0 fichiers")
    assert analyser_icacls(sortie, chemin) == ["builtin\\administrateurs", "sie\\be"]
    assert analyser_icacls(f"{chemin} Tout le monde:(RX)\n", chemin) == ["*"]


def test_droits_fichier_et_autorisation(modele_archives):
    d = DroitsFichier(modele_archives)
    assert d.comptes(modele_archives / CONFIDENTIEL / "Memoire" / "memoire_technique_definitif.docx") == ["sie\\direction"]
    assert d.comptes(modele_archives / EHPAD / "Memoire" / "memoire_technique_definitif.docx") == ["*"]
    assert autorise(DIRECTION, ["sie\\direction"]) and not autorise(BE, ["sie\\direction"])
    assert autorise(Utilisateur("x", ["Direction"]), ["sie\\direction"])     # groupe transmis sans domaine
    assert not autorise(Utilisateur("x", [], admin=True), ["sie\\direction"])  # l'admin ne lit pas tout


# --- synchronisation ---------------------------------------------------------------------
def test_premiere_synchronisation(moteur):
    st = moteur.entrepot.stats()
    assert st["affaires"] == 10 and st["exclus"] == 1          # le CV
    assert st["par_type"] == {"memoire": 14, "prix": 1}
    etat = moteur.etat_synchro.lire()
    assert etat["dernier_succes"] and etat["nb_fichiers"] == 16 and not etat["en_cours"]


def test_synchronisation_incrementale(moteur):
    racine = Path(moteur.config.archives)
    ix = moteur.indexeur
    cr = ix.synchroniser()
    assert (cr.ajoutes, cr.modifies, cr.deplaces, cr.supprimes, cr.inchanges) == (0, 0, 0, 0, 16)
    # modification
    f = racine / GYMNASE / "Memoire" / "memoire_technique_definitif.docx"
    import docx
    d = docx.Document(str(f))
    d.add_heading("7 Variante", 1)
    d.add_paragraph("Nous proposons en variante des projecteurs à optique asymétrique pour limiter l'éblouissement des joueurs.")
    d.save(str(f))
    os.utime(f, (f.stat().st_atime, f.stat().st_mtime + 5))
    cr = ix.synchroniser()
    assert cr.modifies == 1 and cr.ajoutes == 0
    assert any("asymétrique" in b.texte for b in moteur.recherche.chercher("optique asymétrique éblouissement", Filtre(utilisateur=BE), k=3))
    # déplacement (renommage du dossier d'affaire) : rien n'est relu
    shutil.move(str(racine / EHPAD), str(racine / "2017_ehpad_tilleuls_renomme"))
    cr = ix.synchroniser()
    assert cr.deplaces == 1 and cr.ajoutes == 0 and cr.supprimes == 0, cr
    res = moteur.recherche.chercher("continuité du système de sécurité incendie résidents", Filtre(utilisateur=BE), k=3)
    affaires = {b.affaire for b in res}
    assert "2017_ehpad_tilleuls_renomme" in affaires and EHPAD not in affaires
    # suppression
    (racine / GYMNASE / "DPGF_lot_electricite.xlsx").unlink()
    cr = ix.synchroniser()
    assert cr.supprimes == 1
    assert moteur.entrepot.stats()["par_type"].get("prix") is None


def test_changement_de_droits(moteur):
    racine = Path(moteur.config.archives)
    filtre = Filtre(utilisateur=BE)
    assert any(b.affaire == GYMNASE for b in moteur.recherche.chercher("éclairage LED aire de jeu gymnase", filtre, k=5))
    (racine / GYMNASE / "_droits.json").write_text(json.dumps({"lecture": ["SIE\\Direction"]}))
    cr = moteur.indexeur.synchroniser()
    assert cr.droits_modifies >= 2
    assert not any(b.affaire == GYMNASE for b in moteur.recherche.chercher("éclairage LED aire de jeu gymnase", filtre, k=10))


def test_gardes_fous(moteur, tmp_path):
    racine = Path(moteur.config.archives)
    # partage vide ou débranché : on ne vide pas l'index
    vide = tmp_path / "vide"
    vide.mkdir()
    cr = moteur.indexeur.synchroniser(vide)
    assert cr.statut == "echec" and moteur.entrepot.stats()["documents"] == 15
    # plus de 30 % de disparitions : arrêt par prudence
    import rag_sie.indexation as ix
    ix.SUPPRESSIONS_MIN = 5
    for a in AFFAIRES[:6]:
        shutil.rmtree(racine / a["dossier"])
    cr = moteur.indexeur.synchroniser()
    assert cr.statut == "echec" and "prudence" in cr.message
    assert moteur.entrepot.stats()["documents"] == 15
    cr = moteur.indexeur.synchroniser(autoriser_suppressions_massives=True)
    ix.SUPPRESSIONS_MIN = 20
    assert cr.statut == "termine" and cr.supprimes == 9
    # verrou : pas deux synchronisations à la fois
    assert moteur.etat_synchro.prendre()
    assert moteur.indexeur.synchroniser().message == "Une synchronisation est déjà en cours."
    moteur.etat_synchro.rendre()


# --- recherche ---------------------------------------------------------------------------
def test_droits_filtres_a_la_recherche(moteur):
    q = "gestion technique du bâtiment et comptage énergétique décret tertiaire"
    assert not any(b.affaire == CONFIDENTIEL for b in moteur.recherche.chercher(q, Filtre(utilisateur=BE), k=10))
    assert moteur.recherche.chercher(q, Filtre(utilisateur=DIRECTION), k=3)[0].affaire == CONFIDENTIEL
    doc_id = moteur.recherche.chercher(q, Filtre(utilisateur=DIRECTION), k=1)[0].document_id
    assert moteur.entrepot.document(doc_id)["comptes"] == ["sie\\direction"]


def test_brouillons_ecartes_et_doublons(moteur):
    q = "présentation de l'entreprise bureau d'études intégré"
    res = moteur.recherche.chercher(q, Filtre(utilisateur=BE), k=8)
    assert all("brouillon" not in b.fichier for b in res)
    # le même paragraphe figure dans tous les mémoires : montré une seule fois, version la plus récente lisible
    pres = [b for b in res if b.texte.startswith("SIE est une entreprise")]
    assert len(pres) == 1 and pres[0].annee == 2024
    tous = moteur.recherche.chercher(q, Filtre(utilisateur=BE, valides_seulement=False), k=50)
    assert len({b.affaire for b in tous}) >= 1


def test_diversite_et_contexte(moteur):
    res = moteur.recherche.chercher("habilitations électriques et consignation, travaux en hauteur", Filtre(utilisateur=BE), k=8)
    par_affaire = {}
    for b in res:
        par_affaire[b.affaire] = par_affaire.get(b.affaire, 0) + 1
    assert max(par_affaire.values()) <= 2
    sim = moteur.recherche.affaires_similaires("SSI en site occupé avec des résidents", Filtre(utilisateur=BE), k=3)
    assert sim[0]["affaire"] == EHPAD


def test_qualite_sur_le_jeu_demo(moteur):
    r = evaluer(moteur.recherche, JEU_DEMO, k=5)
    assert r["modes"]["hybride"]["rappel@k"] >= 0.9
    assert r["modes"]["hybride"]["mrr"] >= r["modes"]["mots"]["mrr"] - 0.05


# --- rédaction ---------------------------------------------------------------------------
class FauxRedacteur:
    nom, externe = "faux", True

    def __init__(self):
        self.recu = None

    def rediger(self, consigne, question, passages):
        self.recu = passages
        vrai = " ".join(passages[0].texte.split()[:12])
        return Reponse("Nous maintenons le SSI en service. [À COMPLÉTER : effectif]",
                       [Citation(0, vrai), Citation(1, "phrase qui n'existe dans aucune archive de SIE")], cout_usd=0.012)


def test_brouillon_cite_et_journalise(moteur):
    faux = FauxRedacteur()
    d = DemandeSection("Continuité du système de sécurité incendie en site occupé", critere="Valeur technique", ponderation=40,
                       exigences=["maintenir la sécurité des résidents pendant les travaux"], activites=["SSI"], type_ouvrage="santé")
    br = rediger(d, moteur.recherche, faux, Filtre(utilisateur=BE), moteur.journal, BE.login)
    assert br.statut == "propose" and br.a_completer == 1
    assert [c.verifiee for c in br.citations] == [True, False]
    assert any("introuvable" in a for a in br.avertissements)
    assert all(p.titre.split("/")[0] != CONFIDENTIEL for p in faux.recu)       # droits respectés à l'envoi
    assert all("brouillon" not in p.titre for p in faux.recu)
    ligne = moteur.journal.lire()[-1]
    assert ligne["utilisateur"] == BE.login and ligne["passages"] == [p.ref for p in faux.recu] and ligne["cout_usd"] == 0.012
    assert "texte" not in ligne                                                   # le journal ne recopie pas les archives


def test_prix_jamais_envoyes(moteur):
    faux = FauxRedacteur()
    br = rediger(DemandeSection("Tableau général basse tension et projecteurs LED", requete="tableau général projecteurs LED prix"),
                 moteur.recherche, faux, Filtre(utilisateur=BE, types=["memoire", "prix"]), moteur.journal)
    prix = [p for p in br.passages if p["fichier"].endswith(".xlsx")]
    assert prix and not any(p["envoye"] for p in prix)
    assert not any("DPGF" in p.titre for p in faux.recu)


def test_sans_ia_les_passages_restent(moteur):
    br = rediger(DemandeSection("Éclairage de l'aire de jeu"), moteur.recherche, AucunRedacteur(), Filtre(utilisateur=BE), moteur.journal)
    assert br.statut == "passages_seuls" and br.passages and not br.texte
    assert moteur.journal.lire() == []                                            # rien n'est sorti


def test_ia_exterieure_verrouillee(monkeypatch):
    monkeypatch.delenv("SIE_LLM_EXTERNE_AUTORISE", raising=False)
    with pytest.raises(ErreurAutorisation):
        BedrockRedacteur(client=object())


def test_adaptateur_claude_citations():
    bloc = SimpleNamespace(type="text", text="Nous maintenons le SSI.", citations=[
        SimpleNamespace(document_index=0, cited_text="le nouveau SSI fonctionnent en parallèle")])
    rep = SimpleNamespace(stop_reason="end_turn", content=[bloc], usage=SimpleNamespace(input_tokens=10_000, output_tokens=1_000))
    appels = []
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: appels.append(kw) or rep))
    c = _Claude(client, "anthropic.claude-sonnet-5-5", serveur_fallback=False)
    r = c.rediger("consigne", "question", [Passage("1", "titre", "texte")])
    assert r.texte == "Nous maintenons le SSI." and r.citations[0].passage == 0
    assert round(r.cout_usd, 4) == 0.033
    assert appels[0]["messages"][0]["content"][0]["citations"] == {"enabled": True}


def _faux_openai(sortie, status="completed", contenu=None):
    rep = SimpleNamespace(status=status, output_text=sortie,
                          output=[SimpleNamespace(type="message", content=contenu or [SimpleNamespace(type="output_text")])],
                          usage=SimpleNamespace(input_tokens=10_000, output_tokens=1_000,
                                                input_tokens_details=SimpleNamespace(cached_tokens=2_000)))
    appels = []
    return SimpleNamespace(responses=SimpleNamespace(create=lambda **kw: appels.append(kw) or rep)), appels


def test_adaptateur_openai(monkeypatch):
    monkeypatch.setenv("SIE_LLM_EXTERNE_AUTORISE", "oui")
    sortie = json.dumps({"texte": "Nous maintenons le SSI. [À COMPLÉTER : effectif]",
                         "citations": [{"passage": 1, "extrait": "le SSI reste en service"}, {"passage": 9, "extrait": "hors liste"}]})
    client, appels = _faux_openai(sortie)
    o = OpenAIRedacteur("gpt-6.2-sol", client=client)
    r = o.rediger("consigne", "question", [Passage("1", "affaire/memoire.docx p.2", "le SSI reste en service")])
    assert r.erreur is None and r.texte.startswith("Nous maintenons")
    assert [c.passage for c in r.citations] == [0]                             # numéro hors liste ignoré
    assert round(r.cout_usd, 4) == 0.0264                                      # 8 000 × 2 $ + 2 000 × 0,2 $ + 1 000 × 10 $
    a = appels[0]
    assert a["store"] is False and a["model"] == "gpt-6.2-sol"
    assert a["text"]["format"]["strict"] is True and "[1] affaire/memoire.docx p.2" in a["input"]


def test_adaptateur_openai_refus_et_coupure(monkeypatch):
    monkeypatch.setenv("SIE_LLM_EXTERNE_AUTORISE", "oui")
    client, _ = _faux_openai("", contenu=[SimpleNamespace(type="refusal", refusal="non")])
    assert "refusé" in OpenAIRedacteur(client=client).rediger("c", "q", [Passage("1", "t", "x")]).erreur
    client, _ = _faux_openai('{"texte": "début", "cit', status="incomplete")
    assert "interrompue" in OpenAIRedacteur(client=client).rediger("c", "q", [Passage("1", "t", "x")]).erreur
    client, _ = _faux_openai(json.dumps({"texte": "début", "citations": []}), status="incomplete")
    assert "[À COMPLÉTER : brouillon interrompu" in OpenAIRedacteur(client=client).rediger("c", "q", [Passage("1", "t", "x")]).texte


def test_openai_verrouille_et_choix_du_modele(monkeypatch):
    monkeypatch.delenv("SIE_LLM_EXTERNE_AUTORISE", raising=False)
    with pytest.raises(ErreurAutorisation):
        OpenAIRedacteur(client=object())
    monkeypatch.setenv("SIE_LLM_EXTERNE_AUTORISE", "oui")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert redacteur("openai:gpt-6.2-astra").nom == "openai:gpt-6.2-astra"
    assert redacteur("openai").nom == "openai:gpt-6.2-sol"
    assert redacteur("aucun").nom == "aucun"


def test_periode_indexee(tmp_path, modele_archives):
    from rag_sie.config import periode
    assert periode("2022-2026") == (2022, 2026) and periode("") is None and periode("2024") == (2024, 2024)
    archives = tmp_path / "archives"
    shutil.copytree(modele_archives, archives)
    cfg = ConfigRAG(donnees=tmp_path / "donnees", archives=str(archives), droits="fichier", annees=(2022, 2026))
    m = Moteur(cfg, encodeur=EncodeurHachage(), redacteur=AucunRedacteur())
    try:
        assert m.indexeur.synchroniser().statut == "termine"
        annees = {b.annee for b in m.recherche.chercher("installation électrique", Filtre(), k=30)}
        assert annees and all(2022 <= a <= 2026 for a in annees)
    finally:
        m.fermer()


def test_comparaison_a_l_aveugle(moteur, tmp_path):
    from rag_sie.comparaison import comparer, ecrire

    class Autre(FauxRedacteur):
        nom = "autre"
    sections = [DemandeSection("Continuité du SSI en site occupé", exigences=["résidents"]),
                DemandeSection("Colonnes montantes et coordination Enedis")]
    res = comparer(sections, moteur.recherche, [FauxRedacteur(), Autre()], BE, moteur.journal, BE.login)
    assert res["moteurs"] == ["faux", "autre"]
    for ligne in res["sections"]:
        a, b = ligne["essais"]
        assert a["passages_envoyes"] == b["passages_envoyes"]                 # mêmes passages pour les deux IA
        assert {a["lettre"], b["lettre"]} == {"A", "B"}
    assert res["resume"]["faux"]["cout_total_usd"] == 0.024
    d = ecrire(res, tmp_path / "cmp")
    grille = (d / "grille_notation.md").read_text(encoding="utf-8")
    assert "Brouillon A" in grille and "faux" not in grille                   # anonyme


# --- service HTTP ------------------------------------------------------------------------
def test_service(moteur, monkeypatch):
    from fastapi.testclient import TestClient

    from rag_sie.service import creer_app
    moteur.config.jeton = "secret"
    moteur.config.groupes_admin = ["SIE\\Admin-RAG"]
    moteur._redacteur = FauxRedacteur()
    moteur.__dict__.pop("redacteur", None)
    c = TestClient(creer_app(moteur, planifier=False))
    entetes = {"Authorization": "Bearer secret", "X-SIE-Utilisateur": "SIE\\jdupont", "X-SIE-Groupes": "SIE\\BE"}
    assert c.get("/health").json() == {"statut": "ok", "version": "0.1.0"}
    assert c.get("/health", headers=entetes).json()["index"]["documents"] == 15
    assert c.post("/search", json={"requete": "colonnes montantes"}).status_code == 401
    r = c.post("/search", headers=entetes, json={"requete": "colonnes montantes Enedis", "k": 3, "elargir": True}).json()
    assert r["resultats"][0]["affaire"] == AFFAIRES[3]["dossier"] and r["resultats"][0]["section"]["texte"]
    assert r["resultats"][0]["chemin_partage"].endswith("memoire_technique_definitif.docx")
    # document d'une affaire confidentielle : invisible pour le bureau d'études
    dir_entetes = {**entetes, "X-SIE-Utilisateur": "SIE\\mdurand", "X-SIE-Groupes": "SIE\\Direction"}
    doc_id = c.post("/search", headers=dir_entetes, json={"requete": "comptage énergétique décret tertiaire", "k": 1}).json()["resultats"][0]["document_id"]
    assert c.get(f"/documents/{doc_id}", headers=dir_entetes).status_code == 200
    assert c.get(f"/documents/{doc_id}", headers=entetes).status_code == 404
    # synchronisation réservée aux profils autorisés
    assert c.post("/sync", headers=entetes).status_code == 403
    admin = {**entetes, "X-SIE-Groupes": "SIE\\BE;SIE\\Admin-RAG"}
    assert c.post("/sync", headers=admin).status_code == 202
    import time
    for _ in range(100):
        if not c.get("/sync/status", headers=admin).json()["en_cours"]:
            break
        time.sleep(0.05)
    assert c.get("/sync/status", headers=admin).json()["compte_rendu"]["statut"] == "termine"
    br = c.post("/draft", headers=entetes, json={"titre": "Continuité du SSI en site occupé", "exigences": ["résidents"]}).json()
    assert br["statut"] == "propose" and br["citations"][0]["verifiee"]
    assert c.get("/journal", headers=entetes).status_code == 403
    assert len(c.get("/journal", headers=admin).json()["envois"]) == 1
