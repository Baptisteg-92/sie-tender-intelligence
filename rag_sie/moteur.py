"""Assemblage des briques à partir de la configuration : un seul objet pour la ligne de commande et le service."""
from __future__ import annotations

from functools import cached_property
from pathlib import Path

from .config import ConfigRAG
from .droits import lecteur_droits
from .encodeurs import encodeur as choisir_encodeur
from .encodeurs import reclasseur as choisir_reclasseur
from .entrepot import ouvrir
from .indexation import EtatSynchro, Indexeur
from .recherche import Recherche
from .redacteurs import Redacteur
from .redacteurs import redacteur as choisir_redacteur
from .redaction import Journal

VERSION = "0.1.0"


class Moteur:
    def __init__(self, config: ConfigRAG | None = None, encodeur=None, redacteur: Redacteur | None = None):
        self.config = config or ConfigRAG.depuis_env()
        self.config.donnees.mkdir(parents=True, exist_ok=True)
        self._encodeur, self._redacteur = encodeur, redacteur

    @cached_property
    def encodeur(self):
        return self._encodeur or choisir_encodeur(self.config.encodeur)

    @cached_property
    def entrepot(self):
        return ouvrir(self.config.base, self.encodeur.nom, self.encodeur.dimension)

    @cached_property
    def recherche(self) -> Recherche:
        return Recherche(self.entrepot, self.encodeur, choisir_reclasseur(self.config.reclasseur))

    @cached_property
    def indexeur(self) -> Indexeur:
        return Indexeur(self.entrepot, self.encodeur, lecteur_droits(self.config.droits, Path(self.config.archives or ".")),
                        self.config)

    @cached_property
    def redacteur(self) -> Redacteur:
        """Créé à la première demande : une IA extérieure non autorisée lève une erreur explicite à ce moment-là."""
        return self._redacteur or choisir_redacteur()

    @cached_property
    def journal(self) -> Journal:
        return Journal(self.config.donnees)

    @property
    def etat_synchro(self) -> EtatSynchro:
        return EtatSynchro(self.config.donnees)

    def fermer(self) -> None:
        if "entrepot" in self.__dict__:
            self.entrepot.fermer()
