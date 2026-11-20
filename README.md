# SIE Tender Intelligence

Outil d'aide à la réponse aux appels d'offres pour SIE (projet commande entreprise, IMT Atlantique, 2026).
Il couvre le parcours complet du cahier des charges, du dépôt du DCE au dossier prêt à relire.

| Étape | Fonction | Module |
| --- | --- | --- |
| Import du DCE, classement des pièces | | `sie_tender/ingestion.py` |
| Fiche de renseignement GO / NO GO | F1 | `sie_tender/f1_fiche.py` |
| Exigences, critères, RSE, interfaces, pièces à fournir | F2 | `sie_tender/f2_exigences.py` |
| Import du métré Excel du bureau d'études | F3 | `sie_tender/f3_quantites.py` |
| Brouillons de demandes de cotation | F4 | `sie_tender/f4_cotations.py` |
| Devis, association aux postes, DPGF provisoire | F5 | `sie_tender/f5_dpgf.py` |
| Archives, recherche, trame du mémoire | F6 | `sie_tender/f6_archives.py` |
| Contrôle final et export du dossier | F7 | `sie_tender/f7_controle.py` |
| Modèle de données commun | | `sie_tender/models.py` |
| Interface | | `app.py` (Streamlit) |

## Organisation de l'interface (maquette V2)

L'interface suit la maquette V2 réalisée par le groupe 2 avec SIE et le document « Spécifications fonctionnelles et techniques détaillées » du 10/11/2026. Un sélecteur commun dans la barre latérale choisit la consultation traitée ; toutes les rubriques travaillent sur elle, sans mélange entre dossiers.

| Rubrique | Contenu |
| --- | --- |
| Tableau de bord | consultations actives, échéances, décisions en attente, alertes, devis attendus, DPGF et mémoires à finaliser, actions prioritaires avec lien direct |
| Consultations | recherche, filtre par statut, responsable, archivage, accès aux étapes |
| Chargement du DCE | dépôt manuel (fichiers, ZIP ou dossier du serveur SIE), inventaire des pièces, PDF de la fiche |
| Analyse du DCE | registre de vigilance (priorité, source et page, action, responsable, échéance, statut, destination, « à intégrer au mémoire »), synthèse PDF, lots retenus, interfaces, exigences, critères, assistant IA |
| Fiche de renseignement | rubriques du modèle SIE (dont architecte, coordonnateurs SPS et SSI, visite sur rendez-vous, fiche de visite signée), tous les lots, GO / NO GO par la direction avec historique, responsable de visite choisi dans le référentiel, PDF imprimable |
| Décomptes & métrés | XLSX, XLS ou CSV ; choix de la feuille et des colonnes ; doublons, unités ambiguës, quantités manquantes ; plusieurs imports par lot, fichiers sources versionnés |
| Demandes de prix | un fournisseur du référentiel, ses seuls articles, extrait du CCTP sur plusieurs plages de pages non contiguës, bordereau Excel, brouillon d'e-mail, suivi (brouillon, prêt, envoyé par SIE, relancé, réponse reçue) et versions |
| Chiffrage / DPGF | devis rattachés aux demandes, comparatif par poste, **choix manuel** du fournisseur avec motif (jamais de moins-disant automatique), DPGF avec feuille « Suivi fournisseurs » |
| Mémoire technique | choix « un mémoire commun » ou « un mémoire par lot », points de vigilance à intégrer, export Word par lot, bibliothèque des anciens mémoires (chemin UNC, synchronisation quotidienne et bouton « Synchroniser maintenant ») |
| Documents | pièces de candidature et d'offre, reçues puis validées, rattachées à un fichier déposé |
| Contrôle & export | anomalies bloquantes et à vérifier (dont alertes bloquantes non levées et livrables à revalider), validations technique / chiffrage / administrative, « prêt à déposer » impossible avec une anomalie bloquante sauf dérogation de la direction, ZIP par candidature / offre / lot |
| Paramètres | fournisseurs et contacts (désactivés plutôt que supprimés), utilisateurs et rôles (sans mot de passe), personnes pour les visites |

Les référentiels sont enregistrés dans `<espace>/_referentiels.json`, hors des dossiers d'affaires. Quand une donnée amont validée change (quantités, choix d'un fournisseur, points du registre), les livrables aval concernés sont signalés « à revalider ».

### Synchronisation de la bibliothèque d'anciens mémoires

```bash
python -m sie_tender synchroniser --espace affaires --index archives_index.json
```

À planifier chaque nuit (02:00 par défaut) dans le planificateur de tâches du serveur SIE. Le chemin du partage, les sous-dossiers et la règle « mémoires validés » se règlent dans la rubrique Mémoire. Un verrou évite deux traitements simultanés ; sans changement sur le partage, rien n'est refait. Quand un fichier change, l'index par mots-clés est reconstruit en entier. Le moteur RAG du noyau IA est étudié à part.

## Règles de conception

- **Rien n'est inventé.** Chaque information extraite porte sa source (fichier, page, article). Une donnée absente est marquée « à vérifier ».
- **SIE décide.** GO / NO GO, lots retenus, fournisseurs, prix, temps de pose, frais, aléas et marges sont saisis par SIE. Sans ces coefficients, le prix de vente reste vide.
- **Confidentialité.** L'application et ses données sont hébergées sur le serveur de SIE. SIE a validé l'usage d'un LLM par API (novembre 2026) : l'administrateur l'active sur le serveur avec `SIE_LLM_EXTERNE_AUTORISE=oui` et la clé d'API ; les pages utiles du DCE et des archives sont alors envoyées au fournisseur d'IA. Sans ce réglage, rien ne sort du serveur. Aucun document SIE ne doit être ajouté à ce dépôt (voir `.gitignore`).
- **Rien n'est envoyé.** Les demandes de prix et le dossier final sont des fichiers à relire ; l'envoi et le dépôt restent manuels.

## Installation

```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
```

OCR des PDF scannés (facultatif) : installer Tesseract avec la langue française (`tesseract-ocr` et `tesseract-ocr-fra`).

## Essai rapide sur les données fictives

```bash
python scripts/generer_donnees_fictives.py exemples     # DCE, archives, métré et devis inventés
python scripts/generer_dce_test.py exemples/dce_test      # 3 autres DCE : lot unique, 3 lots, DCE incomplet
python scripts/demo_bout_en_bout.py exemples            # parcours F1 à F7 en ligne de commande
python scripts/evaluer.py                                # qualité de l'extraction et temps, contre scripts/attendus/
python -m pytest -q                                      # tests
```

## Interface

```bash
python -m sie_tender indexer exemples/archives_fictives --index archives_index.json
streamlit run app.py
```

Puis dans le navigateur : « Chargement du DCE » pour déposer un DCE (ou « Consultations › Démonstration »), puis les rubriques dans l'ordre. Chaque page propose l'étape suivante en bas.

Parcours prévu par le cahier des charges :

- **Rôles** (barre latérale) : Direction, Bureau d'études, Chargé d'affaires, Responsable technique, Dossier final. Seule la direction enregistre le GO / NO GO ; chaque validation garde le nom et le rôle (historique sur la fiche et au contrôle).
- **Fiche (F1)** : tous les lots avec les activités SIE repérées (CFO, CFA, SSI, CVC, plomberie, désenfumage), autres intervenants (BET, contrôleur technique, SPS, OPC). Les lots ne se cochent qu'après GO ; le collaborateur chargé de la visite et la date de visite se saisissent alors. Export dans le modèle Excel de SIE : balises `{{reference}}`, `{{objet}}`, `{{decision}}`, `{{responsable_visite}}`… et une ligne `{{lot_numero}} {{lot_intitule}} {{lot_retenu}}` répétée pour chaque lot.
- **Analyse (F2)** : matrice d'interfaces lot × lot tirée des CCTP de tous les lots et des pièces communes, synthèse des seuls lots retenus (prestations, exigences, risques, interfaces, RSE dont heures d'insertion, questions) exportable en Word.
- **Mémoire (F6)** : si le DCE contient un cadre de réponse, ses chapitres forment le plan, dans l'ordre ; sinon les critères du RC. Les passages repris gardent leur source, les références d'affaires comparables se choisissent, les pièces jointes (planning, notes de calcul, synoptiques) s'ajoutent, et la liste des compléments à fournir s'exporte en Word.

## Version téléchargeable (gratuite)

Chaque étiquette `v*` poussée sur GitHub publie une archive dans l'onglet **Releases** du dépôt (workflow `release.yml`).

```bash
git tag v0.2.0 && git push --tags   # ou : onglet Actions > Version téléchargeable > Run workflow
```

Pour l'utilisateur : installer Python 3.11+, décompresser l'archive, puis double-cliquer sur `lancer_windows.bat` (Windows) ou `lancer_mac.command` (Mac ; si macOS bloque le fichier : clic droit, Ouvrir). Le premier lancement installe les dépendances. Les données restent sur l'ordinateur, dans le dossier `affaires/`.

## Version web (gratuite, Streamlit Community Cloud)

1. Se connecter sur [share.streamlit.io](https://share.streamlit.io) avec le compte GitHub propriétaire du dépôt.
2. **Create app** → dépôt `Keiler-ux/sie-tender-intelligence`, branche `main`, fichier `app.py`.
3. **Advanced settings** → Python 3.12, et dans **Secrets** :
   ```toml
   SIE_MOT_DE_PASSE = "un-mot-de-passe-long"
   SIE_DEMO = "1"
   ```
4. **Deploy**. Dans les réglages de l'application, garder la visibilité privée et inviter les membres de l'équipe.

À savoir : le stockage y est temporaire (les affaires disparaissent au redémarrage) et les fichiers sont hébergés chez Streamlit. Cette version est une démonstration : **aucun document SIE** sans l'accord écrit de SIE.

## Docker (serveur SIE ou autre hébergeur)

```bash
docker build -t sie-tender .
docker run -p 8501:8501 -v sie_donnees:/data -e SIE_MOT_DE_PASSE=... sie-tender
```

`SIE_DEMO=1` ajoute les données fictives. Les affaires sont conservées dans le volume `/data`.

## Ligne de commande

```bash
python -m sie_tender analyser chemin/vers/dce.zip --index archives_index.json --sortie resultats/
python -m sie_tender chercher "intervention en site occupé" --index archives_index.json
```

## IA (facultative)

L'outil fonctionne sans IA. Quand elle est activée, elle ajoute :

- **Assistant IA** : synthèse du DCE pour la décision GO / NO GO, et questions libres sur le DCE ;
- **Fiche** : complément des champs restés « à vérifier » ;
- **Mémoire** : brouillon de section à partir des passages d'archives et des exigences du DCE, avec des `[À COMPLÉTER]` là où l'information manque (une section ne peut pas être validée tant qu'il en reste).

Chaque réponse cite les pages du DCE utilisées, et chaque citation est recherchée mot pour mot dans les pièces : celles qu'on ne retrouve pas sont signalées « à vérifier ».

| Moteur | Réglages (variables d'environnement ou secrets Streamlit) | À savoir |
| --- | --- | --- |
| Aucun (défaut) | — | règles uniquement |
| Claude | `ANTHROPIC_API_KEY`, `SIE_LLM_EXTERNE_AUTORISE = "oui"`, `SIE_LLM = "anthropic"` ; facultatif : `SIE_LLM_MODELE` (défaut `claude-opus-5-5`), `SIE_LLM_EFFORT` (`low`, `medium`, `high`), `ANTHROPIC_WORKSPACE_ID` (si la clé n'est rattachée à aucun espace de travail) | API externe et payante, validée par SIE. Activée pour tout le serveur par l'administrateur. Coût estimé affiché après chaque réponse. |
| Ollama | `SIE_LLM = "ollama"`, `SIE_LLM_MODELE` (ex. `mistral`) | gratuit, rien ne sort du poste ; version téléchargeable uniquement, PC avec 16 Go de RAM conseillés |

Le moteur se choisit aussi dans la barre latérale (« Configurer l'IA ») parmi ceux qui sont disponibles.

## Limites connues

- Les règles d'extraction ont été mises au point sur un DCE fictif : elles devront être ajustées sur les consultations historiques de SIE.
- La recherche dans les archives est lexicale (BM25). Une recherche par vecteurs pourra être ajoutée si les essais le justifient.
- L'association devis / postes est une proposition à valider ; la lecture des devis PDF ne gère que des lignes simples.
- Le format de la fiche suit les champs du cahier des charges ; l'export au modèle SIE attend que SIE fournisse son fichier.
- Le score de `scripts/evaluer.py` sur les DCE fictifs ne mesure rien de réel (les règles ont été écrites sur ces DCE). Il servira sur les consultations historiques de SIE, avec des attendus écrits à la main et gardés hors du dépôt.
- Les rôles ne sont pas des comptes : chacun choisit son rôle dans la barre latérale. Le référentiel des utilisateurs existe, mais l'authentification Windows / Active Directory reste à brancher en production.
- Changer de consultation dans le sélecteur ne prévient pas des saisies non enregistrées : enregistrer avant de changer.
- Les bibliothèques de l'interface sont celles de Streamlit, installées sur le serveur (pas de CDN).
