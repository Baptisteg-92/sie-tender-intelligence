"""Vecteurs de sens et reclassement, calculés en local.

- bge-m3 (BAAI, licence MIT) : multilingue, bon en français, 1024 dimensions, tourne sur processeur.
  Le modèle est téléchargé une fois (ou copié depuis une clé USB) dans SIE_RAG_MODELES, puis
  utilisé hors ligne : aucune donnée ne sort du serveur.
- bge-reranker-v2-m3 (facultatif) : relit les 30 meilleurs passages et les reclasse plus finement.
- hachage : vecteurs approximatifs sans aucune dépendance, pour les tests et les démonstrations.
  Il rapproche les mots de même racine, pas les synonymes : ne pas l'utiliser en production.

Changer d'encodeur oblige à réindexer : l'entrepôt refuse un mélange de vecteurs.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol

import numpy as np

from .texte import jetons


class Encodeur(Protocol):
    nom: str
    dimension: int

    def encoder(self, textes: list[str], requete: bool = False) -> np.ndarray: ...


def _normer(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (m / n).astype(np.float32)


class EncodeurHachage:
    nom = "hachage-v1"
    dimension = 512

    def _vecteur(self, texte: str) -> np.ndarray:
        v = np.zeros(self.dimension, dtype=np.float32)
        for mot in jetons(texte):
            for trait, poids in [(mot, 1.0)] + [(mot[i:i + 4], 0.35) for i in range(max(len(mot) - 3, 0))]:
                h = int.from_bytes(hashlib.blake2b(trait.encode(), digest_size=8).digest(), "little")
                v[h % self.dimension] += poids if (h >> 40) & 1 else -poids
        return v

    def encoder(self, textes: list[str], requete: bool = False) -> np.ndarray:
        if not textes:
            return np.zeros((0, self.dimension), dtype=np.float32)
        return _normer(np.stack([self._vecteur(t) for t in textes]))


def _chemin_modele(nom_hf: str) -> str:
    """Dossier local du modèle s'il existe (SIE_RAG_MODELES/<nom>), sinon le nom Hugging Face."""
    base = os.getenv("SIE_RAG_MODELES")
    if base:
        local = Path(base) / nom_hf.split("/")[-1]
        if local.exists():
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            return str(local)
    return nom_hf


class EncodeurBgeM3:
    nom = "bge-m3"
    dimension = 1024

    def __init__(self, appareil: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.modele = SentenceTransformer(_chemin_modele("BAAI/bge-m3"), device=appareil or os.getenv("SIE_RAG_APPAREIL"))
        self.modele.max_seq_length = 1024

    def encoder(self, textes: list[str], requete: bool = False) -> np.ndarray:
        if not textes:
            return np.zeros((0, self.dimension), dtype=np.float32)
        return self.modele.encode(textes, batch_size=16, normalize_embeddings=True, convert_to_numpy=True).astype(np.float32)


class Reclasseur:
    nom = "bge-reranker-v2-m3"

    def __init__(self):
        from sentence_transformers import CrossEncoder

        self.modele = CrossEncoder(_chemin_modele("BAAI/bge-reranker-v2-m3"), max_length=1024)

    def scores(self, requete: str, textes: list[str]) -> list[float]:
        if not textes:
            return []
        return [float(x) for x in self.modele.predict([(requete, t) for t in textes], batch_size=16)]


def encodeur(nom: str = "auto") -> Encodeur:
    if nom in ("hachage", "hachage-v1"):
        return EncodeurHachage()
    if nom == "auto":
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            return EncodeurHachage()
    return EncodeurBgeM3()


def reclasseur(nom: str = "aucun") -> Reclasseur | None:
    if not nom or nom == "aucun":
        return None
    return Reclasseur()
