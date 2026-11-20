"""Recherche hybride dans les archives.

1. deux listes de candidats, chacune filtrée par les droits de l'utilisateur :
   - par les mots (plein texte, racinisé, sans accents) : trouve les termes exacts, les références
     de normes, les noms de matériels ;
   - par le sens (vecteurs bge-m3) : trouve « chauffage urbain » pour « réseau de chaleur » ;
2. fusion des deux classements (RRF : chaque liste vote selon le rang, pas selon le score brut) ;
3. reclassement facultatif par un modèle local plus fin ;
4. léger bonus aux passages de la même activité, du même type d'ouvrage, et récents
   (de l'ordre d'une ou deux places de classement : il départage, il ne renverse pas) ;
5. diversité : au plus 2 passages par affaire, et les paragraphes recopiés d'un mémoire à l'autre
   ne sont montrés qu'une fois (on garde la version la plus récente).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .encodeurs import Encodeur, Reclasseur
from .entrepot import BlocTrouve, Filtre
from .texte import normaliser

K_RRF = 60
# Bonus de contexte, exprimé en « places » de classement : il départage des passages proches,
# il ne fait pas passer un passage moyen devant un passage nettement meilleur.
UNITE_RRF = 1 / (K_RRF + 1) - 1 / (K_RRF + 3)
UNITE_RECLASSEMENT = 0.25


@dataclass
class Contexte:
    """Ce que l'on sait du nouvel appel d'offres, pour favoriser les passages comparables."""
    activites: list[str] = field(default_factory=list)
    type_ouvrage: str | None = None
    texte: str = ""   # objet du marché, lots retenus… ajouté à la requête par le sens


def fusion_rrf(listes: list[list[tuple[int, float]]], k: int = K_RRF) -> dict[int, float]:
    scores: dict[int, float] = {}
    for liste in listes:
        for rang, (i, _) in enumerate(liste, 1):
            scores[i] = scores.get(i, 0.0) + 1.0 / (k + rang)
    return scores


def _empreinte_texte(t: str) -> str:
    return normaliser(t)[:240]


class Recherche:
    def __init__(self, entrepot, encodeur: Encodeur, reclasseur: Reclasseur | None = None):
        self.entrepot, self.encodeur, self.reclasseur = entrepot, encodeur, reclasseur

    def candidats(self, requete: str, filtre: Filtre, n: int = 40, mode: str = "hybride",
                  requete_sens: str | None = None) -> list[BlocTrouve]:
        listes = []
        rangs: dict[str, dict[int, int]] = {}
        if mode in ("hybride", "mots"):
            t = self.entrepot.chercher_texte(requete, filtre, n)
            listes.append(t)
            rangs["mots"] = {i: r for r, (i, _) in enumerate(t, 1)}
        if mode in ("hybride", "sens"):
            v = self.entrepot.chercher_vecteur(self.encodeur.encoder([requete_sens or requete], requete=True)[0], filtre, n)
            listes.append(v)
            rangs["sens"] = {i: r for r, (i, _) in enumerate(v, 1)}
        fus = fusion_rrf(listes)
        ids = sorted(fus, key=lambda i: -fus[i])[:n]
        out = self.entrepot.blocs(ids)
        for b in out:
            b.score = fus[b.id]
            b.scores = {"rrf": round(fus[b.id], 5), **{k: r.get(b.id) for k, r in rangs.items()}}
        return out

    def chercher(self, requete: str, filtre: Filtre, k: int = 8, contexte: Contexte | None = None,
                 max_par_affaire: int = 2, mode: str = "hybride") -> list[BlocTrouve]:
        contexte = contexte or Contexte()
        requete_sens = f"{requete}\n{contexte.texte}".strip() if contexte.texte else None
        cands = self.candidats(requete, filtre, 40, mode, requete_sens)
        if self.reclasseur and cands:
            sc = self.reclasseur.scores(requete, [f"{b.chemin_titres}\n{b.texte}" for b in cands[:30]])
            for b, s in zip(cands, sc):
                b.scores["reclassement"] = round(s, 4)
                b.score = s
            cands = sorted(cands[:30], key=lambda b: -b.score) + cands[30:]
        annee = date.today().year
        unite = UNITE_RECLASSEMENT if self.reclasseur else UNITE_RRF
        for b in cands:
            points = 0.0
            if contexte.activites and set(b.activites) & set(contexte.activites):
                points += 1
            if contexte.type_ouvrage and b.type_ouvrage == contexte.type_ouvrage:
                points += 1
            if b.annee and annee - b.annee <= 5:
                points += 0.5
            b.score += points * unite
        cands.sort(key=lambda b: -b.score)
        return self.diversifier(cands, k, max_par_affaire)

    @staticmethod
    def diversifier(cands: list[BlocTrouve], k: int, max_par_affaire: int) -> list[BlocTrouve]:
        # Paragraphes identiques dans plusieurs mémoires : on garde le plus récent.
        par_empreinte: dict[str, BlocTrouve] = {}
        for b in cands:
            e = _empreinte_texte(b.texte)
            if e not in par_empreinte or (b.annee or 0) > (par_empreinte[e].annee or 0):
                par_empreinte[e] = b
        gardes = {id(b) for b in par_empreinte.values()}
        out, par_affaire = [], {}
        for b in cands:
            if id(b) not in gardes:
                continue
            if par_affaire.get(b.affaire, 0) >= max_par_affaire:
                continue
            par_affaire[b.affaire] = par_affaire.get(b.affaire, 0) + 1
            out.append(b)
            if len(out) >= k:
                break
        return out

    def section_complete(self, b: BlocTrouve) -> dict | None:
        s = self.entrepot.section(b.document_id, b.section_id)
        return {"titres": s[0], "texte": s[1]} if s else None

    def affaires_similaires(self, description: str, filtre: Filtre, k: int = 5) -> list[dict]:
        """Affaires passées les plus proches : somme des 3 meilleurs passages de chacune."""
        cands = self.candidats(description, filtre, 100)
        par_affaire: dict[str, list[BlocTrouve]] = {}
        for b in cands:
            par_affaire.setdefault(b.affaire, []).append(b)
        out = []
        for affaire, bl in par_affaire.items():
            bl.sort(key=lambda b: -b.score)
            out.append({"affaire": affaire, "score": round(sum(b.score for b in bl[:3]), 5), "annee": bl[0].annee,
                        "type_ouvrage": bl[0].type_ouvrage, "activites": bl[0].activites, "passages": len(bl),
                        "exemple": {"fichier": bl[0].fichier, "page": bl[0].page, "titres": bl[0].chemin_titres}})
        return sorted(out, key=lambda x: -x["score"])[:k]
