"""Mesure de la qualité de la recherche sur un jeu de questions dont on connaît les bonnes réponses.

Format du jeu (JSON) :
    [{"question": "gestion des déchets de chantier, tri sélectif",
      "pertinents": ["2023_college_jean_moulin/memoire_technique_definitif.docx", "2021_ehpad_les_tilleuls"]}]
Un élément de « pertinents » désigne un fichier (affaire/fichier) ou toute une affaire.

Mesures, pour chaque mode (mots seuls, sens seul, hybride) :
- rappel@k : part des questions dont au moins un passage pertinent est dans les k premiers ;
- MRR      : moyenne de 1/rang du premier passage pertinent (1 = toujours en tête).
Le jeu doit être construit avec le bureau d'études sur de vraies questions (30 à 50 suffisent).
"""
from __future__ import annotations

import json
from pathlib import Path

from .entrepot import Filtre
from .recherche import Recherche


def _pertinent(b, pertinents: list[str]) -> bool:
    return any(b.fichier == p or b.affaire == p or b.fichier.startswith(p.rstrip("/") + "/") for p in pertinents)


def evaluer(recherche: Recherche, jeu: list[dict], k: int = 5, modes=("mots", "sens", "hybride")) -> dict:
    resultats = {}
    for mode in modes:
        trouves, rr, details = 0, 0.0, []
        for q in jeu:
            res = recherche.chercher(q["question"], Filtre(types=q.get("types")), k=k, mode=mode, max_par_affaire=k)
            rang = next((i for i, b in enumerate(res, 1) if _pertinent(b, q["pertinents"])), None)
            trouves += rang is not None
            rr += 1 / rang if rang else 0
            details.append({"question": q["question"], "rang": rang})
        n = max(len(jeu), 1)
        resultats[mode] = {"rappel@k": round(trouves / n, 3), "mrr": round(rr / n, 3), "details": details}
    return {"k": k, "questions": len(jeu), "modes": resultats}


def charger_jeu(chemin: str | Path) -> list[dict]:
    return json.loads(Path(chemin).read_text(encoding="utf-8"))


def tableau(r: dict) -> str:
    lignes = [f"{r['questions']} questions, k = {r['k']}", f"{'mode':10s} {'rappel@k':>9s} {'MRR':>6s}"]
    for mode, m in r["modes"].items():
        lignes.append(f"{mode:10s} {m['rappel@k']:9.2f} {m['mrr']:6.2f}")
    return "\n".join(lignes)
