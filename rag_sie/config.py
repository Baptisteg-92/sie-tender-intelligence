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
    SIE_RAG_ANNEES          période indexée, ex. 2022-2026 (décision SIE du 26/11/2026). Un document dont
                            l'année est connue et hors période est écarté ; année inconnue = gardé et signalé.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MOTIF_VALIDES_DEFAUT = r"valid|d[ée]finitif|final|rendu|envoy"


def _liste(v: str | None) -> list[str]:
    return [x.strip() for x in (v or "").split(";") if x.strip()]


def charger_fichier(chemin: str | Path) -> int:
    """Lit un fichier « CLÉ=valeur » (voir configuration.exemple) et remplit les variables d'environnement
    absentes. Les lignes vides et celles qui commencent par # sont ignorées. Renvoie le nombre de clés lues."""
    n = 0
    for ligne in Path(chemin).read_text(encoding="utf-8-sig").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, val = ligne.partition("=")
        os.environ.setdefault(cle.strip(), val.strip())
        n += 1
    return n


def periode(v: str | None) -> tuple[int, int] | None:
    """« 2022-2026 » → (2022, 2026) ; « 2022 » → (2022, 2022) ; vide → pas de filtre."""
    v = (v or "").strip()
    if not v:
        return None
    debut, _, fin = v.replace("–", "-").partition("-")
    a, b = int(debut), int(fin or debut)
    return (min(a, b), max(a, b))


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
    annees: tuple[int, int] | None = None
    extensions: tuple[str, ...] = (".pdf", ".docx", ".txt", ".md", ".xlsx", ".xlsm")

    def __post_init__(self):
        self.donnees = Path(self.donnees)
        if isinstance(self.annees, str):
            self.annees = periode(self.annees)
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
            annees=periode(e("SIE_RAG_ANNEES", "")),
        )
        valeurs.update({k: v for k, v in surcharges.items() if v is not None})
        return cls(**valeurs)
