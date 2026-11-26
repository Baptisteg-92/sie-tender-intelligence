"""Relecture de la fiche par l'IA, classement des pièces douteuses, règle « aucun calcul »."""
from __future__ import annotations

from sie_tender.f1_fiche import _relire_llm
from sie_tender.models import Champ, FicheRenseignement, Page, Piece, Source, Statut, TypePiece
from sie_tender.pipeline import classer_avec_ia
from sie_tender.texte import nombres_absents

RC = Piece(fichier="RC.pdf", type=TypePiece.RC, pages=[Page(numero=1, texte=(
    "Article 4 : la date limite de remise des offres est fixée au 12 janvier 2027 à 12h00. "
    "Le lieu d'exécution des travaux est le collège Jules Verne à Nantes."))])


class FauxLLM:
    nom, externe, contexte_max = "faux", True, 100_000

    def __init__(self, rep):
        self.rep, self.recu = rep, []

    def json(self, consigne, contenu):
        self.recu.append(contenu)
        return self.rep


def test_relecture_corrige_complete_et_respecte_sie():
    f = FicheRenseignement()
    f.date_limite_remise = Champ(valeur="12 décembre 2026", statut=Statut.EXTRAIT,
                                 source=Source(piece_id=RC.id, fichier="RC.pdf", page=1, extrait="x"))
    f.objet = Champ(valeur="Rénovation électrique", statut=Statut.VALIDE)          # validé par SIE : intouchable
    llm = FauxLLM({"champs": {
        "date_limite_remise": {"valeur": "12 janvier 2027 à 12h00", "fichier": "RC.pdf", "page": 1,
                               "extrait": "la date limite de remise des offres est fixée au 12 janvier 2027 à 12h00", "motif": "correction"},
        "lieu": {"valeur": "collège Jules Verne à Nantes", "fichier": "RC.pdf", "page": 1,
                 "extrait": "Le lieu d'exécution des travaux est le collège Jules Verne à Nantes"},
        "objet": {"valeur": "autre chose", "fichier": "RC.pdf", "page": 1, "extrait": "Le lieu d'exécution des travaux est le collège"},
        "procedure": {"valeur": "appel d'offres ouvert", "fichier": "RC.pdf", "page": 1, "extrait": "phrase absente des pièces du dossier"}}})
    journal = []
    _relire_llm(f, [RC], llm, journal)
    assert f.date_limite_remise.valeur == "12 janvier 2027 à 12h00"
    assert f.date_limite_remise.valeur_avant == "12 décembre 2026" and f.date_limite_remise.statut == Statut.A_VERIFIER
    assert f.lieu.statut == Statut.EXTRAIT and f.lieu.methode == "llm"
    assert f.objet.valeur == "Rénovation électrique"                                  # non modifié
    assert f.procedure.valeur is None                                                 # citation introuvable : ignoré
    assert "objet" not in llm.recu[0].split("Pièces :")[0]                            # champ validé non envoyé
    assert any("ignorée" in j for j in journal)


def test_classement_ia_seulement_pour_autre():
    p = Piece(fichier="Piece_3.pdf", type=TypePiece.ANNEXE, pages=[Page(numero=1, texte="Document de travail, lot 2")])
    conflit = Piece(fichier="RC_v2.pdf", type=TypePiece.RC, pages=[Page(numero=1, texte="Cahier des clauses administratives particulières")])
    manuel = Piece(fichier="x.pdf", type=TypePiece.AUTRE, type_methode="saisie", pages=[Page(numero=1, texte="texte")])
    llm = FauxLLM({"type": "CCTP"})
    journal = []
    classer_avec_ia([RC, p, manuel, conflit], llm, journal)
    assert p.type == TypePiece.CCTP and p.type_methode == "llm"
    assert manuel.type == TypePiece.AUTRE and len(llm.recu) == 2                     # RC sûr et choix manuel non envoyés
    assert any("désaccord" in j for j in journal)


def test_nombres_absents():
    sources = ["Délai d'exécution : 14 semaines. Montant estimé 120 000 € HT."]
    assert nombres_absents("Le délai est de 14 semaines pour 120 000 € HT, en 3 phases.", sources) == []
    assert nombres_absents("Soit 98 jours, ou 2,5 mois, sur [4].", sources) == ["98", "2.5"]


def test_brouillon_passe_par_le_service_rag():
    from sie_tender import ia
    from sie_tender.models import Consultation, SectionMemoire

    class FauxRAG:
        def __init__(self):
            self.demande = None

        def rediger(self, demande):
            self.demande = demande
            return {"statut": "propose", "texte": "Nous trions les déchets. [À COMPLÉTER : filière]", "moteur": "openai:gpt-6.2-sol",
                    "cout_usd": 0.02, "avertissements": ["1 extrait cité introuvable"],
                    "citations": [{"fichier": "2023_x/memoire.docx", "page": 4, "extrait": "tri à la source", "verifiee": True}]}

    c = Consultation(dossier="d")
    rag = FauxRAG()
    r = ia.brouillon_section(c, SectionMemoire(titre="Gestion des déchets", ponderation=20), FauxLLM({}), rag=rag)
    assert r.moteur == "rag:openai:gpt-6.2-sol" and r.citations[0].verifiee and r.avertissements
    assert rag.demande["titre"] == "Gestion des déchets" and rag.demande["ponderation"] == 20


def test_pdf_endommage_ne_bloque_pas_l_import(tmp_path):
    from sie_tender.ingestion import importer_dce
    (tmp_path / "RC.txt").write_text("Règlement de la consultation\nRéférence : X-1", encoding="utf-8")
    (tmp_path / "CCTP_lot01.pdf").write_bytes(b"pas un vrai pdf")
    pieces = {p.fichier: p for p in importer_dce(tmp_path)}
    assert pieces["RC.txt"].lisible
    assert not pieces["CCTP_lot01.pdf"].lisible and "illisible" in pieces["CCTP_lot01.pdf"].remarque


def test_avancement_de_l_analyse(tmp_path):
    from sie_tender.llm import AucunLLM
    from sie_tender.pipeline import analyser_dce
    for nom in ("RC.txt", "CCAP.txt", "CCTP_lot01.txt"):
        (tmp_path / nom).write_text(f"{nom}\nRéférence : X-1", encoding="utf-8")
    vus = []
    analyser_dce(tmp_path, llm=AucunLLM(), avancement=lambda f, m: vus.append((f, m)))
    fractions = [f for f, _ in vus]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert any("3/3" in m for _, m in vus)
