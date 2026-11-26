"""Paramètres et référentiels communs à toutes les consultations.

    affaires/_referentiels.json
      fournisseurs      sociétés, spécialités, contacts (désactivées plutôt que supprimées)
      utilisateurs      comptes collaborateurs et rôles (aucun mot de passe stocké)
      visites           personnes aptes aux visites de site
      bibliotheque      configuration de la bibliothèque d'anciens mémoires (partage Windows)

Le fichier est hors des dossiers d'affaires : il n'est jamais mélangé aux données d'une consultation.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from .models import nouvel_id

ROLES = ["Direction", "Bureau d'études", "Chargé d'affaires", "Responsable technique", "Dossier final", "Administrateur"]
SPECIALITES = ["CFO", "CFA", "SSI", "CVC", "Plomberie", "Désenfumage", "Éclairage", "Câbles et conduits",
               "Tableaux et protections", "Courants faibles", "Autre"]
_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(\.[\w-]+)+$")


def email_valide(e: str | None) -> bool:
    return bool(e) and bool(_EMAIL.match(e.strip()))


class Contact(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("CT"))
    nom: str = ""
    prenom: str = ""
    fonction: str = ""
    telephone: str = ""
    email: str = ""
    domaine: str = ""

    @property
    def libelle(self) -> str:
        return f"{self.prenom} {self.nom}".strip() or self.email or "(contact sans nom)"


class Fournisseur(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("FO"))
    raison_sociale: str
    specialites: list[str] = Field(default_factory=list)
    adresse: str = ""
    telephone: str = ""
    email: str = ""
    actif: bool = True
    contacts: list[Contact] = Field(default_factory=list)
    historique: list[str] = Field(default_factory=list)  # consultations où le fournisseur a été sollicité


class Utilisateur(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("US"))
    nom: str = ""
    prenom: str = ""
    societe: str = "SIE"
    identifiant: str = ""
    email: str = ""
    role: str = ROLES[1]
    actif: bool = True

    @property
    def libelle(self) -> str:
        return f"{self.prenom} {self.nom}".strip() or self.identifiant


class PersonneVisite(BaseModel):
    id: str = Field(default_factory=lambda: nouvel_id("PV"))
    nom: str = ""
    prenom: str = ""
    fonction: str = ""
    telephone: str = ""
    secteur: str = ""
    disponibilite: str = ""
    actif: bool = True

    @property
    def libelle(self) -> str:
        return f"{self.prenom} {self.nom}".strip() or "(sans nom)"


class ConfigBibliotheque(BaseModel):
    """Bibliothèque d'anciens mémoires : lecture seule sur le partage SIE, index local."""
    chemin: str = ""                 # de préférence un chemin UNC : \\\\serveur\\partage\\memoires
    heure_synchro: str = "02:00"     # tâche quotidienne (planificateur de tâches Windows)
    sous_dossiers: bool = True
    valides_seulement: bool = True
    motif_valides: str = r"valid|remis|definitif|final"  # règle provisoire, à confirmer avec SIE
    derniere_synchro: Optional[datetime] = None
    dernier_resultat: str = ""
    nb_fichiers: int = 0
    erreurs: list[str] = Field(default_factory=list)


class Referentiels(BaseModel):
    fournisseurs: list[Fournisseur] = Field(default_factory=list)
    utilisateurs: list[Utilisateur] = Field(default_factory=list)
    visites: list[PersonneVisite] = Field(default_factory=list)
    bibliotheque: ConfigBibliotheque = Field(default_factory=ConfigBibliotheque)

    @classmethod
    def charger(cls, chemin: str | Path) -> "Referentiels":
        chemin = Path(chemin)
        if chemin.exists():
            return cls.model_validate_json(chemin.read_text(encoding="utf-8"))
        return cls()

    def sauver(self, chemin: str | Path) -> None:
        chemin = Path(chemin)
        chemin.parent.mkdir(parents=True, exist_ok=True)
        tmp = chemin.with_suffix(".tmp")
        tmp.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(chemin)  # écriture atomique

    def fournisseur(self, fid: str | None) -> Fournisseur | None:
        return next((f for f in self.fournisseurs if f.id == fid), None)

    def fournisseurs_pour(self, famille: str | None) -> list[Fournisseur]:
        """Fournisseurs actifs, ceux dont une spécialité correspond à la famille d'abord. Aucun n'est choisi d'office."""
        actifs = [f for f in self.fournisseurs if f.actif]
        if not famille:
            return actifs
        n = famille.lower()
        return sorted(actifs, key=lambda f: not any(s.lower() in n or n in s.lower() for s in f.specialites))
