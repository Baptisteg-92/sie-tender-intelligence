"""Synchronisation de la bibliothèque d'anciens mémoires (partage Windows SIE, lecture seule).

- tâche quotidienne : `python -m sie_tender synchroniser` planifiée à l'heure choisie (planificateur de tâches Windows) ;
- bouton « Synchroniser maintenant » dans l'interface, pour les profils autorisés ;
- un verrou empêche deux traitements simultanés ;
- un manifeste (chemin, taille, date de modification) permet de ne rien refaire quand rien n'a changé
  et de compter ajouts, modifications et suppressions.

Limite actuelle : quand un fichier change, l'index de recherche par mots-clés est reconstruit en entier
(quelques secondes à quelques minutes selon le volume). Le moteur RAG du noyau IA est étudié à part.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from .f6_archives import Bibliotheque
from .referentiels import ConfigBibliotheque

EXTENSIONS = {".pdf", ".docx", ".doc", ".txt", ".xlsx"}


def _garder(cfg: ConfigBibliotheque, racine: Path):
    motif = re.compile(cfg.motif_valides, re.I) if cfg.valides_seulement and cfg.motif_valides else None

    def garder(f: Path) -> bool:
        rel = f.relative_to(racine)
        if not cfg.sous_dossiers and len(rel.parts) > 2:  # affaire/fichier seulement
            return False
        if f.suffix.lower() not in EXTENSIONS or f.name.startswith(("~$", ".")):
            return False
        if motif and "memoire" in f.name.lower().replace("é", "e") and not motif.search(str(rel)):
            return False  # brouillon de mémoire présumé : écarté selon la règle provisoire
        return True
    return garder


def manifeste(cfg: ConfigBibliotheque) -> dict[str, list]:
    racine = Path(cfg.chemin)
    g = _garder(cfg, racine)
    out = {}
    for f in racine.rglob("*"):
        if f.is_file() and len(f.relative_to(racine).parts) >= 2 and g(f):
            st = f.stat()
            out[str(f.relative_to(racine))] = [st.st_size, int(st.st_mtime)]
    return out


def synchroniser(cfg: ConfigBibliotheque, index: str | Path, forcer: bool = False) -> str:
    """Met l'index à jour si le partage a changé. Renvoie un compte rendu ; met à jour cfg (dernier succès, erreurs)."""
    index = Path(index)
    racine = Path(cfg.chemin) if cfg.chemin else None
    if racine is None or not racine.is_dir():
        cfg.erreurs = [f"Dossier introuvable ou inaccessible : {cfg.chemin or '(non renseigné)'}"]
        cfg.dernier_resultat = "échec"
        return cfg.erreurs[0]
    verrou = index.with_suffix(".verrou")
    if verrou.exists() and time.time() - verrou.stat().st_mtime < 3 * 3600:
        return "Une synchronisation est déjà en cours."
    verrou.write_text(str(os.getpid()))
    try:
        fichier_manifeste = index.with_suffix(".manifeste.json")
        avant = json.loads(fichier_manifeste.read_text(encoding="utf-8")) if fichier_manifeste.exists() else {}
        apres = manifeste(cfg)
        ajouts = len(apres.keys() - avant.keys())
        suppr = len(avant.keys() - apres.keys())
        modifs = sum(1 for k in apres.keys() & avant.keys() if apres[k] != avant[k])
        cfg.nb_fichiers = len(apres)
        if not forcer and index.exists() and not (ajouts or suppr or modifs):
            msg = f"Aucun changement ({len(apres)} fichiers suivis)."
        else:
            b = Bibliotheque.indexer(racine, garder=_garder(cfg, racine))
            b.sauver(index)
            fichier_manifeste.write_text(json.dumps(apres), encoding="utf-8")
            msg = (f"{ajouts} ajout(s), {modifs} modification(s), {suppr} suppression(s) ; "
                   f"{len(b.documents)} documents et {len(b.morceaux)} passages indexés.")
        cfg.derniere_synchro, cfg.dernier_resultat, cfg.erreurs = datetime.now(), msg, []
        return msg
    except Exception as e:  # le partage peut disparaître en cours de lecture
        cfg.erreurs = [str(e)]
        cfg.dernier_resultat = "échec"
        return f"Échec : {e}"
    finally:
        verrou.unlink(missing_ok=True)
