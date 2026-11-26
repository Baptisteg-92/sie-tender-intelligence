"""Brouillon d'une section de mémoire, appuyé sur les archives et cité.

Déroulé pour une section (ex. « 3.2 Gestion des déchets de chantier », critère valeur technique 40 %) :

1. recherche des meilleurs passages d'anciens mémoires validés, que l'utilisateur a le droit de lire ;
2. on ne garde pour l'IA que les passages « envoyables » (jamais de prix, jamais de document écarté),
   au plus 8 et 24 000 caractères ;
3. l'IA rédige un brouillon adapté au nouvel appel d'offres, en citant ses sources, et met
   [À COMPLÉTER : …] partout où il faut une donnée propre à l'affaire (effectifs, noms, dates,
   matériels, quantités) ;
4. chaque extrait cité est recherché mot pour mot dans les passages envoyés : sinon il est
   marqué « non vérifié » ;
5. l'envoi est inscrit au journal (qui, quand, quelle section, quels passages, combien de caractères,
   quel coût) : SIE peut contrôler à tout moment ce qui est sorti.

Le brouillon reste « proposé » : rien n'est validé sans relecture du bureau d'études.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from .entrepot import BlocTrouve, Filtre
from .recherche import Contexte, Recherche
from .redacteurs import Passage, Redacteur
from .texte import nombres_absents, normaliser

MAX_PASSAGES = 8
MAX_CARACTERES = 24_000

CONSIGNE = """Tu aides le bureau d'études de SIE, entreprise d'électricité (courants forts, courants faibles, SSI),
à rédiger une section du mémoire technique d'une réponse à un appel d'offres public.

Tu disposes de passages d'anciens mémoires de SIE. Règles impératives :
- Appuie-toi uniquement sur ces passages et sur les informations du nouvel appel d'offres données dans la demande.
- Adapte le texte au nouvel appel d'offres : ne recopie pas les noms d'anciens clients, d'anciens chantiers ni leurs dates.
- N'invente aucune référence, aucun effectif, aucune certification, aucun matériel, aucune date, aucun engagement chiffré.
  À chaque endroit où une donnée propre à cette affaire est nécessaire, écris [À COMPLÉTER : ce qu'il faut préciser].
- Réponds point par point aux exigences de l'acheteur listées dans la demande ; si les passages ne permettent pas
  d'en traiter une, écris [À COMPLÉTER : exigence non couverte par les archives] à sa place.
- Ne fais aucun calcul (somme, durée, date, pourcentage) : recopie les nombres tels qu'ils sont écrits ; si un calcul
  serait utile, écris [À COMPLÉTER : calcul à faire par le bureau d'études].
- Style de SIE : phrases simples, factuelles, à la première personne du pluriel (« nous »), sans superlatifs
  ni formules publicitaires.
- Écris directement le texte de la section, en français, sans introduction ni commentaire sur ta démarche."""


@dataclass
class DemandeSection:
    titre: str
    consigne: str = ""                    # précision du bureau d'études (« insister sur le tri à la source »)
    critere: str | None = None            # critère de notation du RC
    ponderation: float | None = None
    exigences: list[str] = field(default_factory=list)   # exigences / vigilances du DCE à traiter
    objet_marche: str = ""
    lot: str | None = None
    activites: list[str] = field(default_factory=list)
    type_ouvrage: str | None = None
    requete: str | None = None            # requête de recherche imposée, sinon déduite du titre
    k: int = MAX_PASSAGES


@dataclass
class CitationVerifiee:
    bloc_id: str
    fichier: str
    page: int
    extrait: str
    verifiee: bool


@dataclass
class Brouillon:
    titre: str
    texte: str = ""
    statut: str = "propose"               # propose | passages_seuls | erreur
    moteur: str = "aucun"
    passages: list[dict] = field(default_factory=list)
    citations: list[CitationVerifiee] = field(default_factory=list)
    a_completer: int = 0
    cout_usd: float = 0.0
    erreur: str | None = None
    avertissements: list[str] = field(default_factory=list)

    def dict(self) -> dict:
        return asdict(self)


def requete_section(d: DemandeSection) -> str:
    return d.requete or " ".join(x for x in [d.titre, d.critere or "", d.consigne, " ".join(d.exigences[:5])] if x)


def _passage(b: BlocTrouve) -> Passage:
    titre = f"{b.affaire}/{b.fichier.split('/')[-1]} p.{b.page}" + (f" — {b.chemin_titres}" if b.chemin_titres else "")
    return Passage(ref=str(b.id), titre=titre, texte=b.texte)


def _question(d: DemandeSection) -> str:
    lignes = [f"Section à rédiger : {d.titre}"]
    if d.lot:
        lignes.append(f"Lot : {d.lot}")
    if d.critere:
        lignes.append(f"Critère de notation : {d.critere}" + (f" ({d.ponderation:g} %)" if d.ponderation else ""))
    if d.objet_marche:
        lignes.append(f"Objet du nouveau marché : {d.objet_marche}")
    if d.exigences:
        lignes.append("Exigences et points de vigilance de l'acheteur à traiter :")
        lignes += [f"- {e}" for e in d.exigences]
    if d.consigne:
        lignes.append(f"Précision du bureau d'études : {d.consigne}")
    lignes.append("Rédige la section en citant les passages utilisés.")
    return "\n".join(lignes)


class Journal:
    """journal_envois.jsonl : une ligne par demande de brouillon (le texte des passages n'y est pas recopié)."""

    def __init__(self, dossier: Path):
        self.fichier = Path(dossier) / "journal_envois.jsonl"
        self.fichier.parent.mkdir(parents=True, exist_ok=True)

    def ecrire(self, **ligne) -> None:
        ligne = {"horodatage": datetime.now().isoformat(timespec="seconds"), **ligne}
        with self.fichier.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ligne, ensure_ascii=False) + "\n")

    def lire(self, n: int = 100) -> list[dict]:
        if not self.fichier.exists():
            return []
        lignes = self.fichier.read_text(encoding="utf-8").splitlines()[-n:]
        return [json.loads(x) for x in lignes if x.strip()]


def verifier(citations, passages: list[Passage], blocs: list[BlocTrouve]) -> list[CitationVerifiee]:
    out = []
    for c in citations:
        p, b = passages[c.passage], blocs[c.passage]
        cible = normaliser(c.extrait)
        out.append(CitationVerifiee(p.ref, b.fichier, b.page, c.extrait, len(cible) >= 8 and cible in normaliser(p.texte)))
    return out


def rediger(d: DemandeSection, recherche: Recherche, redacteur: Redacteur, filtre: Filtre, journal: Journal,
            utilisateur: str = "") -> Brouillon:
    contexte = Contexte(activites=d.activites, type_ouvrage=d.type_ouvrage, texte=d.objet_marche)
    filtre.types = filtre.types or ["memoire", "reference"]
    trouves = recherche.chercher(requete_section(d), filtre, k=max(d.k, 1) + 4, contexte=contexte)
    br = Brouillon(titre=d.titre, moteur=redacteur.nom)
    envoyes: list[BlocTrouve] = []
    total = 0
    for b in trouves:
        info = {"bloc_id": b.id, "document_id": b.document_id, "fichier": b.fichier, "affaire": b.affaire, "annee": b.annee,
                "page": b.page, "titres": b.chemin_titres, "texte": b.texte, "score": round(b.score, 5), "envoye": False}
        if not b.envoyable:
            info["motif"] = "jamais transmis (prix ou document protégé)"
        elif len(envoyes) < d.k and total + len(b.texte) <= MAX_CARACTERES:
            envoyes.append(b)
            total += len(b.texte)
            info["envoye"] = True
        br.passages.append(info)
    if not envoyes:
        br.statut = "passages_seuls"
        br.erreur = "Aucun passage d'archive comparable : section à rédiger entièrement par le bureau d'études."
        br.texte = f"[À COMPLÉTER : aucun passage comparable trouvé dans les archives pour « {d.titre} »]"
        br.a_completer = 1
        return br
    passages = [_passage(b) for b in envoyes]
    if redacteur.nom == "aucun":
        for p in br.passages:
            p["envoye"] = False
        br.statut = "passages_seuls"
        br.erreur = redacteur.rediger("", "", []).erreur
        return br
    rep = redacteur.rediger(CONSIGNE, _question(d), passages)
    journal.ecrire(utilisateur=utilisateur, section=d.titre, moteur=redacteur.nom, externe=redacteur.externe,
                   passages=[p.ref for p in passages], fichiers=sorted({b.fichier for b in envoyes}),
                   caracteres=sum(len(p.texte) for p in passages), cout_usd=round(rep.cout_usd, 4), erreur=rep.erreur)
    br.cout_usd = rep.cout_usd
    if rep.erreur:
        br.statut, br.erreur = "erreur", rep.erreur
        return br
    br.texte = rep.texte
    br.citations = verifier(rep.citations, passages, envoyes)
    br.a_completer = len(re.findall(r"\[À COMPLÉTER", br.texte, re.I))
    absents = nombres_absents(br.texte, [p.texte for p in passages] + [_question(d)])
    if absents:
        br.avertissements.append(f"Nombre(s) absent(s) des archives et de la demande, peut-être calculé(s) ou inventé(s) : "
                                 f"{', '.join(absents[:10])}. À vérifier.")
    non_verifiees = sum(1 for c in br.citations if not c.verifiee)
    if not br.citations:
        br.avertissements.append("Le brouillon ne cite aucun passage : à relire avec une attention particulière.")
    if non_verifiees:
        br.avertissements.append(f"{non_verifiees} extrait(s) cité(s) introuvable(s) mot pour mot dans les archives.")
    return br
