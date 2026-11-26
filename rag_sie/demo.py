"""Archives fictives pour les essais et la démonstration (aucune donnée SIE).

Dix affaires inventées, avec ce que l'on trouvera sur le vrai partage :
- un mémoire validé par affaire (Word, titres stylés) ; certains paragraphes sont repris d'un
  mémoire à l'autre, comme dans la réalité ;
- des brouillons de mémoire (à écarter des propositions) ;
- une DPGF (prix : indexée, jamais envoyée à l'IA) et un CV (jamais indexé) ;
- une affaire confidentielle réservée à la direction (_droits.json) ;
- des fiches affaire.json pour une partie des affaires.

    python -m rag_sie.demo <dossier>      crée les archives et affiche le jeu d'évaluation
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PRESENTATION = ("SIE est une entreprise d'électricité générale implantée en Île-de-France. Nous intervenons en courants forts, "
                "courants faibles et sécurité incendie pour des maîtres d'ouvrage publics et privés. Notre bureau d'études "
                "intégré prépare les études d'exécution, les notes de calcul et les dossiers des ouvrages exécutés. "
                "Nos équipes de chantier sont encadrées par un conducteur de travaux et un chef de chantier dédiés à chaque affaire.")

COMMUN = {
    "securite": ("Chaque compagnon reçoit un accueil sécurité à son arrivée sur le chantier. Le plan particulier de sécurité et "
                 "de protection de la santé est rédigé avant le démarrage et mis à jour à chaque changement de phase. "
                 "Les consignations électriques sont réalisées par du personnel habilité B2V et BR, avec cadenassage et "
                 "vérification d'absence de tension. Les travaux en hauteur sont réalisés depuis des plateformes individuelles "
                 "roulantes ou des nacelles, conduites par du personnel titulaire du CACES."),
    "doe": ("Le dossier des ouvrages exécutés est remis sous format numérique et papier à la réception. Il comprend les plans "
            "de récolement, les schémas des tableaux, les notes de calcul, les fiches techniques des matériels posés et les "
            "procès-verbaux d'essais. Nous assurons la formation des exploitants à la prise en main des installations."),
}

AFFAIRES = [
    {"dossier": "2016_college_victor_hugo_Montreuil", "annee": 2016, "type": "enseignement", "act": ["CFO", "CFA"],
     "objet": "rénovation des installations électriques du collège Victor Hugo",
     "specifique": ("Les travaux sont réalisés pendant les vacances scolaires d'été pour les salles de classe et le soir après "
                    "17 h pour les circulations. Un planning par bâtiment est validé avec la principale du collège. "
                    "Les zones de travaux sont isolées par des cloisons de chantier et la continuité de l'éclairage de sécurité "
                    "est maintenue à chaque fin de journée.")},
    {"dossier": "2017_ehpad_les_tilleuls_Bondy", "annee": 2017, "type": "santé", "act": ["CFO", "SSI"],
     "objet": "remplacement du système de sécurité incendie de l'EHPAD Les Tilleuls",
     "specifique": ("Les travaux se déroulent en site occupé, en présence des résidents. La continuité de service du système de "
                    "sécurité incendie est garantie à tout moment : l'ancien et le nouveau SSI fonctionnent en parallèle jusqu'à "
                    "la bascule, réalisée zone par zone avec une ronde de surveillance renforcée. Les interventions bruyantes "
                    "sont proscrites pendant les heures de repos des résidents, entre 13 h et 15 h.")},
    {"dossier": "2018_gymnase_jean_jaures_Pantin", "annee": 2018, "type": "sport", "act": ["CFO"],
     "objet": "éclairage LED et distribution électrique du gymnase Jean Jaurès",
     "specifique": ("L'éclairage de l'aire de jeu est remplacé par des projecteurs LED antichocs assurant un éclairement moyen de "
                    "500 lux pour la compétition régionale, avec une uniformité supérieure à 0,7. Un gradateur permet trois "
                    "niveaux : entraînement, compétition et entretien. Les projecteurs sont posés depuis une nacelle articulée, "
                    "le parquet sportif étant protégé par des panneaux de répartition de charge.")},
    {"dossier": "2019_residence_les_peupliers_Noisy", "annee": 2019, "type": "logement", "act": ["CFO", "CFA"],
     "objet": "réhabilitation des colonnes montantes de 120 logements",
     "specifique": ("Le remplacement des colonnes montantes est coordonné avec Enedis, qui valide le dossier technique et réalise "
                    "les raccordements. Chaque locataire est informé une semaine avant la coupure, limitée à une demi-journée par "
                    "logement. Les tableaux d'abonnés sont mis en conformité avec la norme NF C 15-100 et les gaines palières "
                    "sont recoupées au droit de chaque plancher par un calfeutrement coupe-feu.")},
    {"dossier": "2020_hotel_de_ville_Rosny", "annee": 2020, "type": "administration", "act": ["SSI", "CFO"],
     "objet": "mise en conformité du SSI de catégorie A et du désenfumage de l'hôtel de ville",
     "specifique": ("Le système de sécurité incendie de catégorie A comprend un équipement d'alarme de type 1, une centrale de mise "
                    "en sécurité incendie et la commande des volets de désenfumage. Le coordonnateur SSI valide les analyses "
                    "fonctionnelles avant les essais. Les essais de réception sont réalisés en présence du bureau de contrôle "
                    "et de la commission de sécurité.")},
    {"dossier": "2021_mediatheque_aime_cesaire_Bobigny", "annee": 2021, "type": "culture", "act": ["CFA"],
     "objet": "contrôle d'accès, vidéoprotection et réseau VDI de la médiathèque Aimé Césaire",
     "specifique": ("Le contrôle d'accès par badge équipe les portes de service et les réserves de documents. La vidéoprotection "
                    "comprend douze caméras IP avec enregistrement local de trente jours, conformément à l'autorisation "
                    "préfectorale. Le réseau VDI en catégorie 6A relie les postes de prêt, les bornes d'automates et les points "
                    "d'accès Wi-Fi à une baie de brassage ventilée.")},
    {"dossier": "2022_groupe_scolaire_pasteur_Villemomble", "annee": 2022, "type": "enseignement", "act": ["CFO", "CFA", "SSI"],
     "objet": "construction neuve du groupe scolaire Pasteur",
     "specifique": ("En construction neuve, nos interfaces avec le lot gros œuvre portent sur les réservations et les fourreaux "
                    "noyés en dalle, validés sur plans avant coulage. Le tableau général basse tension est installé dans un local "
                    "dédié et alimente les tableaux divisionnaires de chaque aile. L'école est équipée d'un système d'alarme PPMS "
                    "distinct de l'alarme incendie, avec un signal sonore spécifique pour le confinement.")},
    {"dossier": "2023_centre_hospitalier_Montfermeil", "annee": 2023, "type": "santé", "act": ["CFO"],
     "objet": "alimentation secourue et onduleurs du bloc opératoire",
     "specifique": ("Le bloc opératoire est alimenté par deux sources : le réseau normal et le groupe électrogène, avec un inverseur "
                    "de sources automatique. Les salles d'opération disposent d'un onduleur dédié et d'un réseau IT médical avec "
                    "contrôleur d'isolement. Les coupures sont planifiées avec la direction des soins, de nuit et en dehors de "
                    "toute activité opératoire programmée.")},
    {"dossier": "2024_lycee_jean_moulin_Le_Raincy", "annee": 2024, "type": "enseignement", "act": ["CFA", "CFO"],
     "objet": "restructuration du câblage VDI et des courants forts du lycée Jean Moulin",
     "specifique": ("Le réseau informatique pédagogique est restructuré en catégorie 6A avec trois baies de brassage reliées par "
                    "fibre optique multimode. Les salles informatiques reçoivent des goulottes en allège avec prises de courant "
                    "et prises RJ45. Les travaux en site occupé sont organisés par demi-journées, en accord avec le proviseur et "
                    "le responsable informatique de la région.")},
    {"dossier": "2025_siege_social_confidentiel_Paris", "annee": 2025, "type": "bureaux", "act": ["CFO", "CFA"],
     "objet": "aménagement électrique d'un siège social (affaire confidentielle)",
     "specifique": ("Le plateau de bureaux est équipé d'une gestion technique du bâtiment qui pilote l'éclairage par détection de "
                    "présence et luminosité. Un comptage énergétique par usage, conforme au décret tertiaire, remonte les "
                    "consommations d'éclairage, de prises et de climatisation vers la supervision du client."),
     "droits": ["SIE\\Direction"]},
]

DECHETS = ("Les déchets de chantier sont triés à la source dans des bennes distinctes : déchets inertes, métaux, câbles, "
           "emballages et déchets dangereux comme les tubes fluorescents. Les câbles déposés sont repris par une filière de "
           "valorisation du cuivre et les bordereaux de suivi des déchets sont remis au maître d'œuvre chaque mois.")


def _memoire(a: dict, brouillon: bool = False) -> list[tuple[int, str]]:
    """Paragraphes (niveau de titre, texte) ; niveau 0 = paragraphe courant."""
    p = [(1, f"Mémoire technique : {a['objet']}"),
         (1, "1 Présentation de l'entreprise"), (0, PRESENTATION),
         (1, "2 Organisation du chantier"),
         (2, "2.1 Équipe affectée"),
         (0, "L'équipe est composée d'un conducteur de travaux, d'un chef de chantier et de compagnons électriciens. "
             "[Effectifs et noms à préciser pour chaque affaire.]"),
         (2, "2.2 Contraintes du site"), (0, a["specifique"]),
         (1, "3 Méthodologie d'exécution")]
    if "CFO" in a["act"]:
        p += [(2, "3.1 Courants forts"),
              (0, "Les canalisations sont posées sur chemins de câbles en faux plafond et en goulottes dans les locaux. Les "
                  "tableaux sont livrés précâblés et testés en atelier. Les liaisons équipotentielles sont vérifiées avant mise "
                  "sous tension.")]
    if "CFA" in a["act"]:
        p += [(2, "3.2 Courants faibles"),
              (0, "Les liens VDI sont recettés au testeur certificateur et un rapport de mesures est remis pour chaque prise. "
                  "Les équipements actifs sont fournis par le client sauf mention contraire du CCTP.")]
    if "SSI" in a["act"]:
        p += [(2, "3.3 Sécurité incendie"),
              (0, "Les détecteurs sont implantés selon la norme NF S 61-970 et les déclencheurs manuels à chaque sortie. "
                  "La programmation de la centrale est réalisée par un technicien certifié du constructeur.")]
    p += [(1, "4 Sécurité et prévention"), (0, COMMUN["securite"]),
          (1, "5 Environnement et gestion des déchets"), (0, DECHETS)]
    if not brouillon:
        p += [(1, "6 Dossier des ouvrages exécutés"), (0, COMMUN["doe"])]
    else:
        p += [(0, "Partie à reprendre : version de travail non relue.")]
    return p


def _docx(chemin: Path, paragraphes: list[tuple[int, str]]) -> None:
    import docx

    d = docx.Document()
    for niveau, texte in paragraphes:
        d.add_heading(texte, level=niveau) if niveau else d.add_paragraph(texte)
    chemin.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(chemin))


def generer(dossier: str | Path) -> Path:
    racine = Path(dossier)
    racine.mkdir(parents=True, exist_ok=True)
    for i, a in enumerate(AFFAIRES):
        rep = racine / a["dossier"]
        _docx(rep / "Memoire" / "memoire_technique_definitif.docx", _memoire(a))
        if i % 3 == 0:
            _docx(rep / "Memoire" / "memoire_v1_brouillon.docx", _memoire(a, brouillon=True))
        if i % 2 == 0:
            (rep / "affaire.json").write_text(json.dumps({"annee": a["annee"], "activite": a["act"], "type_ouvrage": a["type"],
                                                         "resultat": "gagné" if i % 4 == 0 else "perdu"}, ensure_ascii=False),
                                              encoding="utf-8")
        if a.get("droits"):
            (rep / "_droits.json").write_text(json.dumps({"lecture": a["droits"]}), encoding="utf-8")
    # Une DPGF (prix) et un CV (jamais indexé)
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Poste", "Désignation", "Unité", "Quantité", "Prix unitaire", "Total"])
    ws.append(["1.1", "Tableau général basse tension", "u", 1, 18500, 18500])
    ws.append(["1.2", "Chemins de câbles 300 mm", "ml", 240, 38, 9120])
    ws.append(["2.1", "Projecteurs LED aire de jeu", "u", 24, 640, 15360])
    wb.save(racine / AFFAIRES[2]["dossier"] / "DPGF_lot_electricite.xlsx")
    _docx(racine / AFFAIRES[6]["dossier"] / "Memoire" / "CV_Jean_Martin_chef_de_chantier.docx",
          [(1, "Curriculum vitae"), (0, "Jean Martin, chef de chantier électricité, 15 ans d'expérience professionnelle.")])
    return racine


# Questions du bureau d'études et affaires où se trouve la bonne réponse.
JEU_DEMO = [
    {"question": "travaux en site occupé avec des résidents âgés, continuité du système incendie", "pertinents": [AFFAIRES[1]["dossier"]]},
    {"question": "maintien du SSI pendant la bascule vers la nouvelle centrale", "pertinents": [AFFAIRES[1]["dossier"]]},
    {"question": "éclairage sportif, niveau d'éclairement et uniformité pour la compétition", "pertinents": [AFFAIRES[2]["dossier"]]},
    {"question": "protection du parquet sportif pendant la pose des luminaires", "pertinents": [AFFAIRES[2]["dossier"]]},
    {"question": "remplacement des colonnes montantes et coordination avec le gestionnaire de réseau", "pertinents": [AFFAIRES[3]["dossier"]]},
    {"question": "information des locataires avant coupure d'électricité", "pertinents": [AFFAIRES[3]["dossier"]]},
    {"question": "SSI catégorie A, CMSI et commande des volets de désenfumage", "pertinents": [AFFAIRES[4]["dossier"]]},
    {"question": "essais de réception en présence de la commission de sécurité", "pertinents": [AFFAIRES[4]["dossier"]]},
    {"question": "vidéosurveillance, durée de conservation des enregistrements", "pertinents": [AFFAIRES[5]["dossier"]]},
    {"question": "badges pour les portes de service et les réserves", "pertinents": [AFFAIRES[5]["dossier"]]},
    {"question": "réservations et fourreaux dans la dalle avant coulage du béton", "pertinents": [AFFAIRES[6]["dossier"]]},
    {"question": "alarme de confinement PPMS dans une école", "pertinents": [AFFAIRES[6]["dossier"]]},
    {"question": "inverseur de sources et groupe électrogène pour un bloc opératoire", "pertinents": [AFFAIRES[7]["dossier"]]},
    {"question": "régime IT médical et contrôleur permanent d'isolement", "pertinents": [AFFAIRES[7]["dossier"]]},
    {"question": "fibre optique entre les baies de brassage d'un lycée", "pertinents": [AFFAIRES[8]["dossier"], AFFAIRES[5]["dossier"]]},
    {"question": "travaux pendant les vacances scolaires et le soir dans un collège", "pertinents": [AFFAIRES[0]["dossier"]]},
    {"question": "tri des déchets et valorisation du cuivre des câbles déposés", "pertinents": [a["dossier"] for a in AFFAIRES[:9]]},
    {"question": "habilitations électriques et consignation", "pertinents": [a["dossier"] for a in AFFAIRES[:9]]},
    {"question": "contenu du dossier des ouvrages exécutés remis à la réception", "pertinents": [a["dossier"] for a in AFFAIRES[:9]]},
    {"question": "recette des prises informatiques au testeur certificateur", "pertinents": [AFFAIRES[i]["dossier"] for i in (0, 3, 5, 6, 8)]},
]


if __name__ == "__main__":
    cible = generer(sys.argv[1] if len(sys.argv) > 1 else "archives_demo")
    (cible.parent / "jeu_evaluation_demo.json").write_text(json.dumps(JEU_DEMO, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Archives fictives créées dans {cible} ; jeu d'évaluation : {cible.parent / 'jeu_evaluation_demo.json'}")
