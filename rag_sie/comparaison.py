"""Comparer deux IA de rédaction (OpenAI et Claude, par exemple) sur les mêmes sections et les mêmes archives.

SIE veut commencer avec OpenAI et comparer plus tard avec Claude sur ses propres documents. Ce module
fait cette comparaison à l'identique : même recherche, mêmes passages envoyés, même consigne ; seule
l'IA change. Le bureau d'études note ensuite chaque brouillon à l'aveugle (A/B), sans savoir qui l'a écrit.

    python -m rag_sie comparer sections.json --moteurs openai anthropic --sortie comparaison/

sections.json : [{"titre": "Gestion des déchets de chantier", "exigences": ["tri à la source"],
                  "objet_marche": "...", "activites": ["CFO"]}, ...]

Sortie : comparaison.json (tout, y compris quelle lettre correspond à quelle IA), grille_notation.md
(brouillons anonymisés à noter) et un résumé chiffré : coût, durée, citations vérifiées, [À COMPLÉTER].
"""
from __future__ import annotations

import json
import random
import time
from dataclasses import asdict
from pathlib import Path

from .entrepot import Filtre
from .redaction import DemandeSection, Journal, rediger
from .redacteurs import redacteur as choisir_redacteur

CRITERES = ["Fidélité aux archives (rien d'inventé)", "Réponse aux exigences de l'acheteur",
            "Qualité de rédaction (style SIE)", "Utilisable après relecture (oui / non)"]


def charger_sections(chemin: str | Path) -> list[DemandeSection]:
    donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
    return [DemandeSection(**d) for d in donnees]


def comparer(sections: list[DemandeSection], recherche, moteurs: list, filtre_utilisateur, journal: Journal,
             utilisateur: str = "", graine: int = 0) -> dict:
    """moteurs : liste d'objets Redacteur (ou de noms « openai », « anthropic:claude-opus-5-5 »…)."""
    reds = [choisir_redacteur(m) if isinstance(m, str) else m for m in moteurs]
    alea = random.Random(graine)
    lignes = []
    for d in sections:
        essais = []
        for red in reds:
            t0 = time.time()
            br = rediger(d, recherche, red, Filtre(utilisateur=filtre_utilisateur), journal, utilisateur)
            cites = br.citations
            essais.append({"moteur": red.nom, "statut": br.statut, "texte": br.texte, "erreur": br.erreur,
                           "duree_s": round(time.time() - t0, 1), "cout_usd": round(br.cout_usd, 4),
                           "citations": len(cites), "citations_verifiees": sum(c.verifiee for c in cites),
                           "a_completer": br.a_completer, "avertissements": br.avertissements,
                           "passages_envoyes": [p["bloc_id"] for p in br.passages if p["envoye"]]})
        ordre = list(range(len(essais)))
        alea.shuffle(ordre)
        lettres = {essais[i]["moteur"]: chr(65 + n) for n, i in enumerate(ordre)}
        for e in essais:
            e["lettre"] = lettres[e["moteur"]]
        lignes.append({"section": asdict(d), "essais": essais})
    return {"moteurs": [r.nom for r in reds], "sections": lignes, "resume": resume(lignes, [r.nom for r in reds])}


def resume(lignes: list[dict], noms: list[str]) -> dict:
    out = {}
    for nom in noms:
        es = [e for l in lignes for e in l["essais"] if e["moteur"] == nom]
        cites = sum(e["citations"] for e in es)
        out[nom] = {"sections": len(es), "erreurs": sum(e["statut"] == "erreur" for e in es),
                    "cout_total_usd": round(sum(e["cout_usd"] for e in es), 4),
                    "duree_moyenne_s": round(sum(e["duree_s"] for e in es) / max(len(es), 1), 1),
                    "citations_verifiees": f"{sum(e['citations_verifiees'] for e in es)}/{cites}",
                    "a_completer": sum(e["a_completer"] for e in es)}
    return out


def grille(resultat: dict) -> str:
    """Grille Markdown anonymisée pour le bureau d'études (les lettres sont tirées au hasard à chaque section)."""
    t = ["# Comparaison à l'aveugle des brouillons", "",
         "Notez chaque brouillon de 1 à 5 sur chaque critère, sans chercher à deviner l'IA. "
         "La correspondance des lettres est dans comparaison.json, à n'ouvrir qu'après la notation.", ""]
    for l in resultat["sections"]:
        t += [f"## {l['section']['titre']}", ""]
        for e in sorted(l["essais"], key=lambda e: e["lettre"]):
            t += [f"### Brouillon {e['lettre']}", "", e["texte"] or f"(pas de brouillon : {e['erreur']})", ""]
            t += ["| Critère | Note (1 à 5) |", "|---|---|"] + [f"| {c} | |" for c in CRITERES] + [""]
    return "\n".join(t)


def ecrire(resultat: dict, dossier: str | Path) -> Path:
    d = Path(dossier)
    d.mkdir(parents=True, exist_ok=True)
    (d / "comparaison.json").write_text(json.dumps(resultat, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "grille_notation.md").write_text(grille(resultat), encoding="utf-8")
    return d
