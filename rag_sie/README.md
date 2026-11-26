# rag_sie : recherche dans les archives SIE et brouillons de mémoire cités

Ce dossier est le noyau IA de SIE Tender Intelligence. Il sert à retrouver, dans 20 ans
d'archives (DCE, mémoires techniques, DPGF, références), les passages utiles à un nouveau
mémoire. Il peut aussi préparer un brouillon de section qui cite ses sources.

- Tout tourne sur le serveur de SIE : lecture du partage Windows, OCR, découpage, index,
  recherche.
- L'IA de rédaction est la seule brique qui peut sortir. Elle est coupée par défaut. SIE a donné
  son accord de principe le 26/11/2026 pour l'API OpenAI, avec un compte ouvert à son nom. Même activée, elle ne reçoit que les quelques
  passages retenus pour la section en cours, et jamais de prix.
- Le bureau d'études relit et valide tout. Le brouillon reste « proposé ».

Le dossier est autonome : aucun `import` vers le reste du dépôt. Pour l'installer dans un autre
dépôt, on copie le dossier `rag_sie/` et on installe `rag_sie/requirements.txt`.

## Organisation

```
partage Windows (lecture seule, compte de service)
        │  chaque nuit à 2 h, ou bouton « Synchroniser maintenant »
        ▼
indexation.py   liste → fichiers nouveaux / modifiés / déplacés / supprimés → droits NTFS
   lecture.py      PDF (texte natif, OCR Tesseract des scans), Word (titres stylés), Excel
   exclusions.py   CV, pièces d'identité, paie, RH : jamais indexés ; prix : jamais envoyés
   metadonnees.py  affaire, année, type de document, activité (CFO, CFA, SSI…), type d'ouvrage, validé
   decoupage.py    sections par chapitre (« 3 Moyens > 3.2 Habilitations »), blocs d'environ 300 mots
   encodeurs.py    vecteurs de sens bge-m3, calculés en local
        ▼
entrepot.py     PostgreSQL + pgvector (serveur)  |  SQLite (poste, pilote, tests)
        ▼
recherche.py    mots + sens → fusion RRF → (reclassement) → diversité par affaire, doublons retirés
redaction.py    passages autorisés et envoyables → IA de rédaction → citations revérifiées → journal
redacteurs.py   aucun (défaut) | openai (choix SIE) | anthropic | bedrock | ollama (modèle local)
comparaison.py  mêmes sections et mêmes passages pour deux IA, grille de notation à l'aveugle
        ▼
service.py      HTTP interne : /sync, /sync/status, /search, /similaires, /draft, /documents/{id}, /journal, /health
```

## Essai rapide (données fictives)

```bash
pip install -r rag_sie/requirements.txt
python -m rag_sie.demo essai/archives          # 10 affaires inventées + jeu d'évaluation
export SIE_RAG_DONNEES=essai/donnees SIE_RAG_ENCODEUR=hachage
python -m rag_sie synchroniser --archives essai/archives
python -m rag_sie chercher "continuité du SSI en site occupé avec des résidents"
python -m rag_sie evaluer essai/jeu_evaluation_demo.json -k 5
SIE_RAG_DEMO=1 python -m rag_sie servir        # http://127.0.0.1:8600/docs
python -m pytest rag_sie/tests -q
```

L'encodeur `hachage` ne demande aucun téléchargement. Il rapproche les mots de même racine, pas
les synonymes. En production, il faut utiliser `bge-m3`, présenté plus bas.

## IA de rédaction : OpenAI, et comparaison avec Claude

Réglages sur le serveur (fichier `configuration.env`) :

```
SIE_LLM=openai
SIE_LLM_MODELE=gpt-6.2-sol          # gpt-6.2-astra pour les sections difficiles, plus cher (×4)
SIE_LLM_EXTERNE_AUTORISE=oui
OPENAI_API_KEY=sk-proj-...          # clé du projet OpenAI de SIE, sur le serveur seulement
# SIE_OPENAI_URL=https://eu.api.openai.com/v1   si OpenAI accorde un projet « Europe »
```

L'adaptateur utilise l'API Responses avec une sortie JSON imposée (texte + citations), `store=False`.
Les extraits cités sont revérifiés mot pour mot dans les passages envoyés, comme pour Claude. Les noms
et tarifs des modèles ont été relevés le 26/11/2026 : à revérifier à l'ouverture du compte.

Pour comparer OpenAI et Claude sur les documents de SIE (même recherche, mêmes passages, seule l'IA change) :

```bash
python -m rag_sie comparer sections.json --moteurs openai anthropic --sortie comparaison
# ou deux modèles d'un même fournisseur : --moteurs openai:gpt-6.2-sol openai:gpt-6.2-astra
```

On obtient `grille_notation.md`, où les brouillons sont anonymisés (A/B tirés au hasard) pour que le
bureau d'études les note sans savoir qui les a écrits, et `comparaison.json` avec le coût, la durée,
les citations vérifiées et le nombre de `[À COMPLÉTER]` de chaque IA.

## Installation sur le serveur SIE

Serveur annoncé par SIE : Lenovo ThinkSystem ST250 V2, Xeon E-2378 (8 cœurs), 64 Go, Windows Server
2025, 2 × 2,4 To SAS + NAS Synology 2 × 6 To, pas de carte graphique. C'est suffisant : bge-m3 tourne
sur le processeur (quelques Go de mémoire), et la rédaction se fait chez OpenAI.

Le script `windows/installer.ps1` fait les étapes 1, 2, 6 et 7 ci-dessous (dossiers, environnement
Python, tâches planifiées, droits sur le fichier de configuration). Il n'a pas encore été exécuté sur
le serveur : à dérouler la première fois avec le prestataire informatique de SIE.

```powershell
powershell -ExecutionPolicy Bypass -File rag_sie\windows\installer.ps1 -Racine D:\SIE\rag -CompteService "SIE\svc-rag"
```

Périmètre : SIE a décidé d'indexer les archives de 2022 à 2026 (`SIE_RAG_ANNEES=2022-2026`). Un
document dont l'année (nom du dossier d'affaire ou `affaire.json`) est hors période est écarté sans
être lu ; un document sans année est gardé et signalé dans les remarques.

1. **Python 3.12** et `pip install -r rag_sie/requirements.txt`.
2. **Recherche par le sens** :
   - installer `torch` (version processeur) puis `sentence-transformers` ;
   - télécharger les modèles sur un poste relié à Internet (`python -m rag_sie.modeles D:\modeles_rag`) ;
   - copier le dossier sur le serveur et indiquer `SIE_RAG_MODELES`. Le serveur ne se connecte
     alors jamais à Internet. Aucune carte graphique n'est nécessaire.
3. **Base** :
   - pour le pilote (30 à 50 affaires), le fichier SQLite par défaut suffit ;
   - pour l'ensemble des archives, il faut PostgreSQL 16 avec l'extension pgvector (≥ 0.6),
     et définir `SIE_RAG_BASE=postgresql://…`. Les tables, l'index HNSW et la configuration
     plein texte française (racines, accents ignorés) sont créés au premier lancement.
4. **OCR** : installer Tesseract avec la langue `fra` pour les PDF scannés. Docling est
   facultatif (`SIE_RAG_DOCLING=1`) : il repère mieux les titres et les tableaux.
5. **Compte de service** : un compte AD en lecture seule sur le partage. Le service relève les
   ACL NTFS de chaque fichier, avec pywin32 si présent, sinon avec la commande `icacls`.
6. **Synchronisation de nuit**, avec le planificateur de tâches Windows :
   ```
   schtasks /Create /TN "SIE RAG synchronisation" /SC DAILY /ST 02:00 /RU SIE\svc-rag ^
     /TR "D:\SIE\rag\.venv\Scripts\python.exe -m rag_sie synchroniser --duree-max 300"
   ```
   Autre solution : `SIE_RAG_HEURE_SYNCHRO=02:00` dans la configuration du service.
7. **Service HTTP** : `python -m rag_sie servir --hote 0.0.0.0 --port 8600`, lancé au démarrage
   (tâche planifiée « au démarrage » ou service Windows via NSSM). Il doit rester sur le réseau
   interne, en HTTPS derrière IIS (proxy inverse) ou avec `uvicorn --ssl-keyfile/--ssl-certfile`.
8. **Configuration** : voir `configuration.exemple` pour toutes les variables, et le détail
   dans `config.py` et `redacteurs.py`.

## Contrat avec l'interface

L'interface (maquette V2 du groupe 2) authentifie l'utilisateur par Active Directory, puis appelle
le service avec trois en-têtes :

```
Authorization: Bearer <SIE_RAG_JETON>
X-SIE-Utilisateur: SIE\jdupont
X-SIE-Groupes: SIE\BE;SIE\Direction
```

```http
POST /search   {"requete": "gestion des déchets", "k": 8, "activites": ["CFO"], "type_ouvrage": "enseignement", "elargir": true}
→ {"resultats": [{"id", "document_id", "fichier", "affaire", "annee", "page", "chemin_titres", "texte",
                  "score", "scores": {"mots": 2, "sens": 1}, "envoyable", "chemin_partage", "section"}], "duree_ms"}

POST /draft    {"titre": "Gestion des déchets", "critere": "Valeur technique", "ponderation": 20,
                "exigences": ["tri à la source", "bordereaux de suivi"], "objet_marche": "...", "activites": ["CFO"]}
→ {"texte", "statut": "propose|passages_seuls|erreur", "citations": [{"fichier", "page", "extrait", "verifiee"}],
   "passages": [... "envoye": true/false], "a_completer", "cout_usd", "avertissements"}

POST /sync  (groupes SIE_RAG_GROUPES_ADMIN)        → 202, ou 409 si une synchronisation tourne déjà
GET  /sync/status                                  → en_cours, progression [fait, total], dernier_succes, nb_fichiers, compte_rendu
GET  /documents/{id}                               → 404 si l'utilisateur n'a pas le droit de lire le fichier
```

Le module F6 de l'outil actuel peut aussi interroger le service. Il suffit de définir
`SIE_RAG_URL` (voir `sie_tender/rag_distant.py`).

## Exigences de SIE couvertes

| Exigence (spécifications du 10/11/2026) | Réponse dans rag_sie |
|---|---|
| 6.1 Données, index et traitements sur l'infrastructure SIE | Lecture, OCR, découpage, vecteurs, index et recherche en local ; modèles chargés depuis le disque |
| 6.1 Aucune transmission à une API externe | Remplacé par l'accord de SIE du 26/11/2026 (API OpenAI). Coupée par défaut ; active seulement avec `SIE_LLM_EXTERNE_AUTORISE=oui`, et seulement pour les passages retenus, jamais les prix |
| 6.1 Partage Windows en lecture seule, chemin UNC | Aucun accès en écriture au partage ; `SIE_RAG_ARCHIVES=\\serveur\partage` |
| 6.2 Synchronisation quotidienne, heure configurable | Tâche planifiée ou `SIE_RAG_HEURE_SYNCHRO` |
| 6.2 Bouton « Synchroniser maintenant » pour les profils autorisés | `POST /sync`, réservé à `SIE_RAG_GROUPES_ADMIN` |
| 6.2 Indexation incrémentale, sans tout réindexer | Taille et date d'abord, empreinte SHA-256 ensuite ; un déplacement ou un renommage n'est pas relu |
| 6.2 Pas de traitements simultanés ; progression, dernier succès, nombre de fichiers, erreurs | Verrou ; `GET /sync/status` ; journal `journal_synchro.jsonl` |
| 6.2 Filtrer ou marquer les brouillons | Règle `SIE_RAG_MOTIF_VALIDES` (ou `affaire.json`), à remplacer par celle de SIE ; brouillons écartés des propositions |
| 6.2 Conserver les ACL, filtrer selon les droits du demandeur | ACL NTFS relevées à chaque nuit et filtrées dans la requête SQL ; document non autorisé = 404 |
| 6.3 Pas de facturation à la page ou à la requête | Logiciels libres (PostgreSQL, pgvector, bge-m3, Tesseract) ; seule l'IA de rédaction est facturée à l'usage |
| 6.3 Journalisation | Synchronisations et envois à l'IA (qui, quand, quels passages, coût), sans recopier les archives |
| 6.3 Rétention et suppression | Un fichier retiré du partage sort de l'index au passage suivant |
| F6 Recherche par activité, type d'ouvrage et exigences | Métadonnées et filtres ; bonus de contexte ; exigences du DCE ajoutées à la requête |
| F6 Rattacher chaque suggestion à sa source | Fichier, page, chemin des titres, chemin sur le partage ; citations revérifiées mot pour mot |
| F6 Aucune invention ; signaler les lacunes | Consigne stricte, `[À COMPLÉTER : …]`, avertissement si une citation est introuvable |
| Règle 8 : références RAG traçables | Identifiant de bloc et de document dans chaque réponse et dans le journal |
| Recette cas H : mémoire validé ajouté au partage, bouton, indexé sans cloud | Testé (`test_synchronisation_incrementale`, `test_service`) |

## Qualité de la recherche

Résultats sur le jeu fictif (20 questions, encodeur `hachage`) :

| Mode | rappel@1 | rappel@5 | MRR@5 |
|---|---|---|---|
| mots seuls | 0,85 | 0,95 | 0,88 |
| sens seul | 0,80 | 1,00 | 0,88 |
| hybride | 0,85 | 1,00 | 0,90 |

SQLite et PostgreSQL donnent les mêmes chiffres, à 0,01 près. Ces chiffres montrent que le montage fonctionne, pas ce qu'il donnera sur les archives réelles.
La prochaine étape est de construire avec le bureau d'études un jeu de 30 à 50 vraies questions,
avec les bons mémoires en face (format dans `evaluation.py`). On mesure ensuite avec `bge-m3`,
avec et sans reclasseur, avant de régler quoi que ce soit.

## Limites actuelles

- `bge-m3` et le reclasseur n'ont pas pu être essayés ici, faute d'accès au site des modèles.
  Les tests utilisent l'encodeur `hachage`.
- La lecture des ACL NTFS (pywin32 ou icacls) n'a été testée que sur des sorties `icacls`
  d'exemple. Elle est à vérifier sur le serveur avec le compte de service.
- La règle des mémoires « validés » est provisoire, en attente de SIE.
- Les fichiers `.doc` (Word ancien) et `.msg` ne sont pas lus. Il faudra les convertir ou
  ajouter LibreOffice en mode sans écran si l'inventaire montre qu'ils sont nombreux.
- L'adaptateur OpenAI est testé avec un faux client (format de la requête, citations, coût, refus,
  réponse coupée), pas encore avec une vraie clé : premier essai à faire avec le compte de SIE, sur
  les archives fictives (`python -m rag_sie.demo`), avant toute archive réelle.
- Le script `windows/installer.ps1` n'a pas encore tourné sur Windows Server 2025.
