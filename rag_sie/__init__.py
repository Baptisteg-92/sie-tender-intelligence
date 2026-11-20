"""Moteur RAG du noyau IA : recherche dans les archives SIE et brouillons de sections cités.

Tout tourne sur le serveur SIE (lecture des archives, OCR, découpage, index, recherche).
Seule la rédaction peut appeler une IA extérieure, et seulement si l'administrateur l'a
autorisée (SIE_LLM_EXTERNE_AUTORISE=oui), avec les seuls passages retenus.

    config.py       réglages (variables d'environnement SIE_RAG_*)
    decoupage.py    découpage des documents par chapitre, avec le chemin des titres
    exclusions.py   documents jamais indexés (CV, pièces d'identité…) ou jamais envoyés (prix)
    droits.py       droits de lecture Windows (NTFS) de chaque fichier
    encodeurs.py    vecteurs de sens (bge-m3 en local) et reclassement facultatif
    entrepot.py     stockage : PostgreSQL + pgvector (serveur), SQLite (poste, tests)
    indexation.py   synchronisation incrémentale du partage (ajouts, modifs, déplacements, suppressions)
    recherche.py    recherche hybride mots + sens, fusion RRF, diversité par affaire
    redaction.py    brouillon de section cité, citations revérifiées, journal des envois
    service.py      service HTTP interne (/sync, /search, /draft…) pour l'interface
    evaluation.py   mesures de qualité de la recherche sur un jeu de questions
    compat.py       adaptateur pour le module F6 existant
"""
