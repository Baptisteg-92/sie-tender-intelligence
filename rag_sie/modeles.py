"""Téléchargement des modèles locaux, à faire une fois sur un poste relié à Internet.

    python -m rag_sie.modeles D:\\modeles_rag

Copier ensuite le dossier sur le serveur SIE et définir SIE_RAG_MODELES=<dossier> : le serveur
charge les modèles depuis le disque, sans jamais se connecter à Internet.
"""
from __future__ import annotations

import sys
from pathlib import Path

MODELES = ["BAAI/bge-m3", "BAAI/bge-reranker-v2-m3"]


def telecharger(dossier: str | Path) -> None:
    from huggingface_hub import snapshot_download

    for nom in MODELES:
        cible = Path(dossier) / nom.split("/")[-1]
        snapshot_download(nom, local_dir=str(cible))
        print(f"{nom} -> {cible}")


if __name__ == "__main__":
    telecharger(sys.argv[1] if len(sys.argv) > 1 else "modeles_rag")
