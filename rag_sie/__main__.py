"""Ligne de commande du moteur RAG.

    python -m rag_sie synchroniser [--archives \\\\serveur\\memoires] [--forcer] [--duree-max 300]
        (à planifier chaque nuit, par exemple 02:00, dans le planificateur de tâches Windows)
    python -m rag_sie etat
    python -m rag_sie chercher "gestion des déchets" [--groupes "SIE\\BE"] [-k 8] [--mode hybride]
    python -m rag_sie rediger "Gestion des déchets de chantier" [--exigence "..."] [--objet "..."]
    python -m rag_sie evaluer jeu.json [-k 5]
    python -m rag_sie comparer sections.json --moteurs openai anthropic [--sortie comparaison]
    python -m rag_sie servir [--hote 0.0.0.0] [--port 8600]

Réglages : variables SIE_RAG_* (voir rag_sie/config.py et README), ou fichier « CLÉ=valeur » désigné par
--config ou par la variable SIE_RAG_CONFIG (sur le serveur : D:\\SIE\\rag\\configuration.env).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from .config import ConfigRAG, charger_fichier
from .droits import Utilisateur
from .entrepot import Filtre
from .moteur import Moteur


def _utilisateur(args) -> Utilisateur:
    return Utilisateur(args.utilisateur or getpass.getuser(), [g for g in (args.groupes or "").split(";") if g])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="rag_sie", description="RAG sur les archives SIE")
    ap.add_argument("--config", help="fichier de configuration CLÉ=valeur")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("synchroniser", help="mettre l'index à jour depuis le partage")
    s.add_argument("--archives")
    s.add_argument("--forcer", action="store_true", help="tout relire (après un changement de règle de découpage)")
    s.add_argument("--sans-droits", action="store_true", help="ne pas relire les droits des fichiers inchangés")
    s.add_argument("--duree-max", type=float, help="arrêter après N minutes (reprise au passage suivant)")
    s.add_argument("--autoriser-suppressions", action="store_true", help="accepter plus de 30 %% de fichiers disparus")
    sub.add_parser("etat", help="état de l'index et de la dernière synchronisation")
    for nom in ("chercher", "rediger"):
        c = sub.add_parser(nom)
        c.add_argument("texte")
        c.add_argument("--utilisateur")
        c.add_argument("--groupes", help="groupes AD séparés par « ; »")
        c.add_argument("-k", type=int, default=8)
        if nom == "chercher":
            c.add_argument("--mode", default="hybride", choices=["hybride", "mots", "sens"])
        else:
            c.add_argument("--exigence", action="append", default=[])
            c.add_argument("--objet", default="")
    e = sub.add_parser("evaluer")
    e.add_argument("jeu")
    e.add_argument("-k", type=int, default=5)
    cp = sub.add_parser("comparer", help="mêmes sections, mêmes passages, deux IA de rédaction (notation à l'aveugle)")
    cp.add_argument("sections")
    cp.add_argument("--moteurs", nargs="+", default=["openai", "anthropic"])
    cp.add_argument("--sortie", default="comparaison")
    cp.add_argument("--utilisateur")
    cp.add_argument("--groupes", help="groupes AD séparés par « ; »")
    v = sub.add_parser("servir")
    v.add_argument("--hote", default="127.0.0.1")
    v.add_argument("--port", type=int, default=8600)
    args = ap.parse_args(argv)

    fichier = args.config or os.environ.get("SIE_RAG_CONFIG")
    if fichier:
        charger_fichier(fichier)
    cfg = ConfigRAG.depuis_env(archives=getattr(args, "archives", None))
    m = Moteur(cfg)
    if args.cmd == "synchroniser":
        cr = m.indexeur.synchroniser(forcer=args.forcer, rafraichir_droits=not args.sans_droits,
                                     duree_max_min=args.duree_max, autoriser_suppressions_massives=args.autoriser_suppressions,
                                     suivi=lambda i, n, f: print(f"\r{i}/{n} {f[:70]:70s}", end="", file=sys.stderr))
        print(file=sys.stderr)
        print(f"[{cr.statut}] {cr.message}")
        for err in cr.erreurs[:20]:
            print("  -", err)
        return 0 if cr.statut in ("termine", "partiel") else 1
    if args.cmd == "etat":
        print(json.dumps({"index": m.entrepot.stats(), "synchro": m.etat_synchro.lire()}, ensure_ascii=False, indent=2, default=str))
        return 0
    if args.cmd == "chercher":
        for b in m.recherche.chercher(args.texte, Filtre(utilisateur=_utilisateur(args)), k=args.k, mode=args.mode):
            print(f"\n[{b.score:.4f}] {b.fichier} p.{b.page} — {b.chemin_titres}\n{b.texte[:500]}")
        return 0
    if args.cmd == "rediger":
        from .redaction import DemandeSection, rediger
        br = rediger(DemandeSection(args.texte, exigences=args.exigence, objet_marche=args.objet, k=args.k),
                     m.recherche, m.redacteur, Filtre(utilisateur=_utilisateur(args)), m.journal, _utilisateur(args).login)
        print(br.texte or br.erreur)
        for c in br.citations:
            print(f"  [{'ok' if c.verifiee else 'NON VÉRIFIÉ'}] {c.fichier} p.{c.page} : « {c.extrait[:100]} »")
        for a in br.avertissements:
            print("  !", a)
        return 0
    if args.cmd == "evaluer":
        from .evaluation import charger_jeu, evaluer, tableau
        print(tableau(evaluer(m.recherche, charger_jeu(args.jeu), k=args.k)))
        return 0
    if args.cmd == "comparer":
        from .comparaison import charger_sections, comparer, ecrire
        u = _utilisateur(args)
        res = comparer(charger_sections(args.sections), m.recherche, args.moteurs, u, m.journal, u.login)
        print(f"Résultats dans {ecrire(res, args.sortie)}")
        print(json.dumps(res["resume"], ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "servir":
        import uvicorn

        from .service import creer_app
        uvicorn.run(creer_app(m), host=args.hote, port=args.port)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
