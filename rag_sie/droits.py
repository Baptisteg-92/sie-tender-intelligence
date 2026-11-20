"""Droits de lecture des fichiers, pour ne montrer à chacun que ce qu'il peut ouvrir.

Chaque document indexé garde la liste des comptes et groupes autorisés à le lire, relevée sur
le partage au moment de la synchronisation. À la recherche, un passage n'est renvoyé que si
l'utilisateur (ou l'un de ses groupes Active Directory) figure dans cette liste.

Trois façons de relever les droits :
- DroitsNTFS     : sur le serveur Windows, lit les ACL NTFS (pywin32, sinon la commande icacls) ;
- DroitsFichier  : pour les essais hors Windows, un fichier _droits.json dans un dossier
                   ({"lecture": ["SIE\\\\BE", "SIE\\\\Direction"]}) vaut pour ce dossier et ses sous-dossiers ;
- DroitsOuverts  : tout le monde lit tout (démonstration seulement).

« * » dans la liste veut dire : tout utilisateur authentifié du domaine.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

TOUS = "*"
# Groupes qui reviennent à « tout le monde » dans un domaine.
_GROUPES_LARGES = {"everyone", "tout le monde", "authenticated users", "utilisateurs authentifies", "utilisateurs authentifiés",
                   "users", "utilisateurs", "domain users", "utilisateurs du domaine"}
# Comptes techniques sans intérêt pour le filtrage.
_IGNORES = {"system", "systeme", "système", "creator owner", "createur proprietaire", "créateur propriétaire",
            "trustedinstaller", "owner rights"}


def _nom_court(compte: str) -> str:
    return compte.split("\\")[-1].strip().lower()


def normaliser_comptes(comptes: list[str]) -> list[str]:
    out: set[str] = set()
    for c in comptes:
        c = c.strip()
        if not c:
            continue
        court = _nom_court(c)
        if c == TOUS or court in _GROUPES_LARGES:
            out.add(TOUS)
        elif court not in _IGNORES:
            out.add(c.lower())
    return sorted(out)


@dataclass
class Utilisateur:
    login: str
    groupes: list[str] = field(default_factory=list)
    admin: bool = False  # peut lancer une synchronisation ; ne donne aucun droit de lecture en plus

    def identites(self) -> set[str]:
        """Login et groupes, avec et sans domaine (« SIE\\BE » et « be »)."""
        out = set()
        for c in [self.login, *self.groupes]:
            if c:
                out.add(c.lower())
                out.add(_nom_court(c))
        return out


def cles_acces(comptes: list[str]) -> list[str]:
    """Clés stockées avec un document : comptes complets et noms courts (« sie\\be » et « be »).

    Le nom court permet de rapprocher « SIE\\BE » (ACL) et « BE » (groupe transmis par l'interface).
    SIE n'ayant qu'un domaine, deux groupes homonymes de domaines différents ne sont pas un cas à traiter.
    """
    return sorted({c for x in comptes for c in (x.lower(), _nom_court(x))})


def autorise(utilisateur: Utilisateur, comptes_document: list[str]) -> bool:
    if TOUS in comptes_document:
        return True
    ids = utilisateur.identites()
    return any(c in ids or _nom_court(c) in ids for c in comptes_document)


class LecteurDroits(Protocol):
    nom: str

    def comptes(self, chemin: Path) -> list[str]: ...


class DroitsOuverts:
    nom = "ouverts"

    def comptes(self, chemin: Path) -> list[str]:
        return [TOUS]


class DroitsFichier:
    """_droits.json le plus proche, en remontant jusqu'à la racine des archives."""
    nom = "fichier"

    def __init__(self, racine: Path):
        self.racine = Path(racine).resolve()
        self._cache: dict[Path, list[str]] = {}

    def vider_cache(self) -> None:
        """Appelé au début de chaque synchronisation : un _droits.json modifié est relu."""
        self._cache.clear()

    def comptes(self, chemin: Path) -> list[str]:
        dossier = Path(chemin).resolve().parent
        while True:
            if dossier in self._cache:
                return self._cache[dossier]
            f = dossier / "_droits.json"
            if f.exists():
                try:
                    valeur = normaliser_comptes(json.loads(f.read_text(encoding="utf-8")).get("lecture", []))
                except (OSError, ValueError):
                    valeur = []  # fichier illisible : personne, par prudence
                self._cache[dossier] = valeur
                return valeur
            if dossier == self.racine or dossier.parent == dossier:
                return [TOUS]
            dossier = dossier.parent


# --- NTFS ------------------------------------------------------------------------
_DROITS_LECTURE = {"F", "M", "RX", "R", "GR", "GA", "RD"}


def analyser_icacls(sortie: str, chemin: str) -> list[str]:
    """Comptes autorisés en lecture d'après la sortie de « icacls <fichier> ».

    Exemple de ligne : « SIE\\BE:(I)(RX) » ; un refus s'écrit « SIE\\Stagiaires:(DENY)(R) ».
    """
    autorises, refuses = [], set()
    for i, ligne in enumerate(sortie.splitlines()):
        if i == 0 and ligne.lower().startswith(chemin.lower()):
            ligne = ligne[len(chemin):]
        ligne = ligne.strip()
        m = re.match(r"^(.+?):((?:\([^)]*\))+)$", ligne)
        if not m:
            continue
        compte, drapeaux = m.group(1).strip(), re.findall(r"\(([^)]*)\)", m.group(2))
        jetons = {x.strip().upper() for d in drapeaux for x in d.split(",")}
        if not jetons & _DROITS_LECTURE:
            continue
        (refuses.add(compte.lower()) if "DENY" in jetons else autorises.append(compte))
    comptes = normaliser_comptes(autorises)
    return [c for c in comptes if c not in refuses]


class DroitsNTFS:
    nom = "ntfs"

    def __init__(self):
        try:
            import win32security  # noqa: F401  (pywin32)
            self._pywin32 = True
        except ImportError:
            self._pywin32 = False

    def _par_pywin32(self, chemin: str) -> list[str]:
        import ntsecuritycon as con
        import win32security as ws

        sd = ws.GetFileSecurity(chemin, ws.DACL_SECURITY_INFORMATION)
        dacl = sd.GetSecurityDescriptorDacl()
        if dacl is None:  # pas de DACL : accès libre
            return [TOUS]
        autorises, refuses = [], set()
        for i in range(dacl.GetAceCount()):
            (type_ace, _drapeaux), masque, sid = dacl.GetAce(i)[:3]
            if not masque & (con.FILE_READ_DATA | con.GENERIC_READ | con.GENERIC_ALL):
                continue
            try:
                nom, domaine, _ = ws.LookupAccountSid(None, sid)
                compte = f"{domaine}\\{nom}" if domaine else nom
            except Exception:
                compte = ws.ConvertSidToStringSid(sid)
            if type_ace == ws.ACCESS_DENIED_ACE_TYPE:
                refuses.add(compte.lower())
            elif type_ace == ws.ACCESS_ALLOWED_ACE_TYPE:
                autorises.append(compte)
        return [c for c in normaliser_comptes(autorises) if c not in refuses]

    def comptes(self, chemin: Path) -> list[str]:
        chemin = str(chemin)
        if self._pywin32:
            return self._par_pywin32(chemin)
        sortie = subprocess.run(["icacls", chemin], capture_output=True, text=True, encoding="oem", errors="replace",
                                check=True).stdout
        return analyser_icacls(sortie, chemin)


def lecteur_droits(mode: str, racine: Path) -> LecteurDroits:
    if mode == "auto":
        mode = "ntfs" if os.name == "nt" else "fichier"
    if mode == "ntfs":
        return DroitsNTFS()
    if mode == "ouverts":
        return DroitsOuverts()
    return DroitsFichier(racine)
