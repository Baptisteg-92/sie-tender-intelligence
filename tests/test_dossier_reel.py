"""Cas tirés de l'essai sur un DCE réel (allotissement en macro-lots, report de date, critères sur plusieurs lignes)."""
from __future__ import annotations

from sie_tender.f1_fiche import construire_fiche
from sie_tender.f2_exigences import extraire_criteres
from sie_tender.ingestion import type_de_piece
from sie_tender.models import Page, Piece, TypePiece

RC = Piece(fichier="X25-1 RC.pdf", type=TypePiece.RC, pages=[
    Page(numero=1, texte="Règlement de la consultation\nConsultation n° MX25-132\n"
                         "Date limite de remise des plis 05 / 10 / 2026 à 15 heures\n"
                         "Les candidats doivent obligatoirement effectuer une visite du site."),
    Page(numero=2, texte="Type N° Objet Codes CPV\n"
                         "Macro lot 1 1.1 Réalisation de Gros-œuvre, curage et \ndémolition\n45223220-4 : Travaux\n"
                         "Macro lot 1 1.2 Réalisation de travaux d'étanchéité 45261420-4 : Travaux\n"
                         "Lot 3 3 Réalisation de travaux d'électricité, CFO, CFA, SSI 45317000-2 : Autres travaux d'installation \n"
                         "électrique\n"
                         "Lot 4 4 Réalisation de travaux pour la pose d'ascenseur 45313100-5 : Travaux\n"),
    Page(numero=3, texte="4. JUGEMENT DES OFFRES\nCritères de notation Pondération\n1. Critère 1 : Technique 60%\n"
                         "1.1 Sous-critère \nMéthodologie de réalisation des travaux, compréhension \ngénérale du site\n30%\n"
                         "1.2 Sous-critère\nMoyens humains 30%\n2. Critère 2 : Prix 40%\n40%"),
])
QR = Piece(fichier="Questions-réponses 06.10.2026.pdf", type=TypePiece.ANNEXE, pages=[
    Page(numero=1, texte="Réponse : La date limite de remise des offres est prolongée jusqu'au 12 octobre 2026 à 15h00.")])


def test_macro_lots_report_de_date_et_visite():
    fiche, lots = construire_fiche([RC, QR])
    assert [l.numero for l in lots] == ["1", "3", "4"]
    assert lots[0].intitule.startswith("Macro-lot 1 : 1.1 Gros-œuvre, curage et démolition ; 1.2")
    assert lots[1].intitule == "Électricité, CFO, CFA, SSI"
    assert fiche.reference.valeur == "MX25-132"
    assert fiche.date_limite_remise.valeur == "12 octobre 2026 à 15h00"
    assert fiche.date_limite_remise.valeur_avant.startswith("05 / 10 / 2026")
    assert fiche.date_limite_remise.statut.value == "a_verifier"
    assert fiche.visite_obligatoire.valeur == "oui" and fiche.visite_obligatoire.source.fichier == RC.fichier


def test_criteres_sur_plusieurs_lignes():
    cr = extraire_criteres([RC])
    assert [(c.intitule, c.ponderation) for c in cr] == [("Technique", 60.0), ("Prix", 40.0)]
    assert [s.ponderation for s in cr[0].sous_criteres] == [30.0, 30.0]
    assert cr[0].sous_criteres[0].intitule == "Méthodologie de réalisation des travaux, compréhension générale du site"


def test_annexes_au_rc_ne_sont_pas_le_rc():
    assert type_de_piece("Annexe 2 au RC Signature electronique.pdf") == TypePiece.ANNEXE
    assert type_de_piece("DC1 note explicative.pdf") == TypePiece.ANNEXE
