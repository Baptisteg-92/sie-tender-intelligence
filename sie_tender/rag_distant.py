"""Branchement du module F6 sur le service RAG (dossier rag_sie/, service HTTP interne).

Si SIE_RAG_URL est défini, l'outil interroge le service au lieu de l'index local par mots-clés :

    SIE_RAG_URL=http://rag.intranet.sie.local:8600
    SIE_RAG_JETON=...                 secret partagé avec le service
    SIE_RAG_UTILISATEUR=SIE\\jdupont   identité transmise (les droits Windows sont appliqués par le service)
    SIE_RAG_GROUPES=SIE\\BE           groupes AD, séparés par « ; »

BibliothequeRAG offre les mêmes méthodes que f6_archives.Bibliotheque (rechercher, affaires_similaires,
documents) : construire_trame et references_similaires fonctionnent sans modification.
Aucune dépendance supplémentaire : appels HTTP de la bibliothèque standard.
"""
from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field

from .models import Passage, Source


@dataclass
class _DocumentDistant:
    affaire: str
    fichier: str = ""
    type: str = "memoire"
    validite: str | None = None
    meta: dict = field(default_factory=dict)


class BibliothequeRAG:
    def __init__(self, url: str, jeton: str = "", utilisateur: str = "sie-tender", groupes: str = "", delai: float = 30):
        self.url, self.delai = url.rstrip("/"), delai
        self.entetes = {"Content-Type": "application/json", "Authorization": f"Bearer {jeton}",
                        "X-SIE-Utilisateur": utilisateur, "X-SIE-Groupes": groupes}
        self._meta: dict[str, dict] = {}

    @classmethod
    def depuis_env(cls) -> "BibliothequeRAG | None":
        url = os.getenv("SIE_RAG_URL")
        if not url:
            return None
        return cls(url, os.getenv("SIE_RAG_JETON", ""), os.getenv("SIE_RAG_UTILISATEUR", "sie-tender"), os.getenv("SIE_RAG_GROUPES", ""))

    def _post(self, chemin: str, corps: dict) -> dict:
        req = urllib.request.Request(self.url + chemin, data=json.dumps(corps).encode(), headers=self.entetes, method="POST")
        with urllib.request.urlopen(req, timeout=self.delai) as r:
            return json.loads(r.read())

    def rechercher(self, requete: str, k: int = 5, score_min: float = 0.0) -> list[Passage]:
        try:
            res = self._post("/search", {"requete": requete, "k": k, "types": ["memoire", "reference"]})["resultats"]
        except Exception:
            return []  # service indisponible : la trame est construite sans passages, comme sans archives
        return [Passage(texte=r["texte"], score=round(r["score"], 4), affaire=r["affaire"],
                        source=Source(piece_id=f"RAG:{r['id']}", fichier=r["fichier"], page=r["page"],
                                      article=r["chemin_titres"] or None, extrait=r["texte"][:300])) for r in res]

    def affaires_similaires(self, description: str, k: int = 3) -> list[tuple[str, float]]:
        try:
            res = self._post("/similaires", {"description": description, "k": k})["affaires"]
        except Exception:
            return []
        for a in res:
            self._meta[a["affaire"]] = {"annee": a.get("annee"), "type": a.get("type_ouvrage"),
                                        "activite": ", ".join(a.get("activites") or [])}
        return [(a["affaire"], a["score"]) for a in res]

    @property
    def documents(self) -> list[_DocumentDistant]:
        return [_DocumentDistant(affaire=a, meta=m) for a, m in self._meta.items()]
