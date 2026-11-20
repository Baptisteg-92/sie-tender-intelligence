"""Réglages du moteur RAG, lus dans les variables d'environnement.

    SIE_RAG_BASE            postgresql://utilisateur:mdp@hote/base   (serveur SIE)
                            ou chemin d'un fichier SQLite             (poste de travail, tests)
                            défaut : <SIE_RAG_DONNEES>/rag.sqlite
    SIE_RAG_ARCHIVES        racine des archives, de préférence en chemin UNC (\\\\serveur\\partage\\memoires)
    SIE_RAG_DONNEES         dossier local du service : verrou, état de synchro, journal des envois
    SIE_RAG_ENCODEUR        bge-m3 (défaut si installé) | hachage (sans dépendance, pour les essais)
    SIE_RAG_RECLASSEUR      bge-reranker-v2-m3 | aucun (défaut)
    SIE_RAG_MOTIF_VALIDES   expression qui reconnaît un mémoire validé dans son chemin
                            (à remplacer par la règle de SIE : dossier, nommage ou statut)
    SIE_RAG_EXCLUSIONS      motifs supplémentaires de fichiers à ne jamais indexer, séparés par « ; »
    SIE_RAG_JETON           secret partagé entre l'interface et le service HTTP
    SIE_RAG_GROUPES_ADMIN   groupes autorisés à lancer une synchronisation, séparés par « ; »
    SIE_RAG_DROITS          auto (défaut) | ntfs | fichier | ouverts
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MOTIF_VALIDES_DEFAUT = r"valid|d[ée]finitif|final|rendu|envoy"


def _liste(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(";") if x.strip()]


@dataclass
class ConfigRAG:
    donnees: Path = Path("rag_donnees")
    base: str = ""
    archives: str = ""
    encodeur: str = "auto"
    reclasseur: str = "aucun"
    motif_valides: str = MOTIF_VALIDES_DEFAUT
    exclusions: list[str] = field(default_factory=list)
    jeton: str = ""
    groupes_admin: list[str] = field(default_factory=list)
    droits: str = "auto"
    extensions: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md", ".xlsx", ".xlsm")

    def __post_init__(self):
        self.donnees = Path(self.donnees)
        if not self.base:
            self.base = str(self.donnees / "rag.sqlite")

    @property
    def postgres(self) -> bool:
        return self.base.startswith(("postgresql://", "postgres://"))

    @classmethod
    def depuis_env(cls, **surcharges) -> "ConfigRAG":
        e = os.environ.get
        valeurs = dict(
            donnees=Path(e("SIE_RAG_DONNEES", "rag_donnees")),
            base=e("SIE_RAG_BASE", ""),
            archives=e("SIE_RAG_ARCHIVES", ""),
            encodeur=e("SIE_RAG_ENCODEUR", "auto"),
            reclasseur=e("SIE_RAG_RECLASSEUR", "aucun"),
            motif_valides=e("SIE_RAG_MOTIF_VALIDES", MOTIF_VALIDES_DEFAUT),
            exclusions=_liste(e("SIE_RAG_EXCLUSIONS")),
            jeton=e("SIE_RAG_JETON", ""),
            groupes_admin=_liste(e("SIE_RAG_GROUPES_ADMIN")),
            droits=e("SIE_RAG_DROITS", "auto"),
        )
        valeurs.update({k: v for k, v in surcharges.items() if v is not None})
        return cls(**valeurs)
