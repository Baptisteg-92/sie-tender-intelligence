"""IA de rédaction : un seul point d'entrée, plusieurs moteurs interchangeables.

    SIE_LLM=aucun       (défaut) pas de rédaction automatique : la recherche reste disponible
    SIE_LLM=openai      API OpenAI (choix de SIE du 26/11/2026, compte ouvert au nom de SIE)
    SIE_LLM=anthropic   Claude par l'API d'Anthropic (pour la comparaison prévue par SIE)
    SIE_LLM=bedrock     Claude sur Amazon Bedrock, région Europe
    SIE_LLM=ollama      modèle local (Mistral…), rien ne sort du serveur

    SIE_LLM_EXTERNE_AUTORISE=oui   obligatoire pour bedrock et anthropic. À n'activer qu'avec
                                    l'accord écrit de SIE (ou sur des documents fictifs).
    SIE_LLM_MODELE                 défaut : gpt-6.2-sol (openai), claude-sonnet-5-5 (anthropic),
                                    anthropic.claude-sonnet-5-5 (bedrock), mistral-small3.2 (ollama)
    OPENAI_API_KEY                 clé du projet OpenAI de SIE (jamais dans le code ni dans le dépôt)
    SIE_OPENAI_URL                 défaut https://api.openai.com/v1 ; https://eu.api.openai.com/v1
                                    si SIE obtient un projet « Europe » (résidence des données)
    SIE_AWS_REGION                 défaut eu-west-3 (Paris) ; identifiants AWS habituels
                                    (variables AWS_*, profil ou rôle du serveur)
    SIE_LLM_EFFORT                 low | medium (défaut) | high
    SIE_OLLAMA_URL                 défaut http://localhost:11434

Chaque moteur reçoit des passages numérotés et renvoie un texte et les extraits qu'il cite.
L'appelant revérifie toujours les extraits dans les passages envoyés.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

# Dollars par million de jetons (entrée, sortie), pour l'estimation du coût affichée et journalisée.
# Tarifs relevés le 26/11/2026, à revérifier : la clé est le nom du modèle passé à l'API.
TARIFS = {"gpt-6.2-sol": (2.0, 10.0), "gpt-6.1-sol": (2.0, 10.0), "gpt-6.2-astra": (8.0, 40.0),
          "gpt-6.1-terra": (1.0, 5.0), "gpt-6-luna": (0.10, 0.50),
          "claude-sonnet-5-5": (2.0, 10.0), "anthropic.claude-sonnet-5-5": (2.2, 11.0),
          "claude-opus-5-5": (4.0, 20.0), "anthropic.claude-opus-5-5": (4.4, 22.0),
          "claude-haiku-5-5": (0.10, 0.50), "anthropic.claude-haiku-5-5": (0.11, 0.55)}


@dataclass
class Passage:
    """Un passage transmis à l'IA."""
    ref: str          # identifiant interne (bloc), jamais envoyé
    titre: str        # « affaire/fichier p.3 — 2 Moyens > 2.1 Habilitations »
    texte: str


@dataclass
class Citation:
    passage: int      # index dans la liste envoyée
    extrait: str


@dataclass
class Reponse:
    texte: str
    citations: list[Citation] = field(default_factory=list)
    cout_usd: float = 0.0
    erreur: str | None = None


class Redacteur(Protocol):
    nom: str
    externe: bool

    def rediger(self, consigne: str, question: str, passages: list[Passage]) -> Reponse: ...


def externe_autorise() -> bool:
    return os.getenv("SIE_LLM_EXTERNE_AUTORISE", "").strip().lower() in ("oui", "1", "true")


class ErreurAutorisation(PermissionError):
    pass


class AucunRedacteur:
    nom = "aucun"
    externe = False

    def rediger(self, consigne: str, question: str, passages: list[Passage]) -> Reponse:
        return Reponse("", erreur="Rédaction automatique désactivée (SIE_LLM=aucun) : les passages sont proposés sans brouillon.")


# --- Claude (API Anthropic ou Bedrock) ---------------------------------------------------
class _Claude:
    externe = True

    def __init__(self, client: Any, modele: str, serveur_fallback: bool):
        self.client, self.modele = client, modele
        self.effort = os.getenv("SIE_LLM_EFFORT", "medium")
        self._fallback = serveur_fallback

    def cout(self, usage: Any) -> float:
        e, s = TARIFS.get(self.modele, (2.2, 11.0))
        u = lambda k: getattr(usage, k, 0) or 0  # noqa: E731
        entree = u("input_tokens") + 1.25 * u("cache_creation_input_tokens") + 0.1 * u("cache_read_input_tokens")
        return (entree * e + u("output_tokens") * s) / 1_000_000

    def _appel(self, **args):
        if self._fallback:
            # En cas de refus du modèle, l'API relance la même requête sur un autre modèle.
            try:
                return self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **args)
            except Exception as e:
                if "fallback" not in str(e).lower():
                    raise
                self._fallback = False
        return self.client.messages.create(**args)

    def rediger(self, consigne: str, question: str, passages: list[Passage]) -> Reponse:
        docs = [{"type": "document", "source": {"type": "text", "media_type": "text/plain", "data": p.texte},
                 "title": p.titre, "citations": {"enabled": True}} for p in passages]
        try:
            rep = self._appel(model=self.modele, max_tokens=8000, system=consigne, output_config={"effort": self.effort},
                              messages=[{"role": "user", "content": docs + [{"type": "text", "text": question}]}])
        except Exception as e:
            return Reponse("", erreur=f"Appel à l'IA de rédaction impossible : {type(e).__name__} {str(e)[:300]}")
        if getattr(rep, "stop_reason", None) == "refusal":
            return Reponse("", cout_usd=self.cout(rep.usage), erreur="L'IA de rédaction a refusé cette demande.")
        morceaux, cites = [], []
        for b in rep.content:
            if getattr(b, "type", "") != "text":
                continue
            morceaux.append(b.text)
            for c in getattr(b, "citations", None) or []:
                i = getattr(c, "document_index", None)
                if isinstance(i, int) and 0 <= i < len(passages):
                    cites.append(Citation(i, (getattr(c, "cited_text", "") or "").strip()))
        texte = "".join(morceaux).strip()
        if getattr(rep, "stop_reason", None) == "max_tokens":
            texte += "\n\n[À COMPLÉTER : brouillon interrompu, trop long]"
        return Reponse(texte, cites, cout_usd=self.cout(rep.usage))


class AnthropicRedacteur(_Claude):
    def __init__(self, modele: str | None = None, client: Any = None):
        if not externe_autorise():
            raise ErreurAutorisation("IA extérieure bloquée : SIE_LLM_EXTERNE_AUTORISE=oui requis (accord écrit de SIE).")
        if client is None:
            import anthropic
            client = anthropic.Anthropic()
        super().__init__(client, modele or os.getenv("SIE_LLM_MODELE", "claude-sonnet-5-5"), serveur_fallback=True)
        self.nom = f"anthropic:{self.modele}"


class BedrockRedacteur(_Claude):
    """Claude sur Amazon Bedrock (client « Mantle »), région européenne par défaut."""

    def __init__(self, modele: str | None = None, client: Any = None, region: str | None = None):
        if not externe_autorise():
            raise ErreurAutorisation("IA extérieure bloquée : SIE_LLM_EXTERNE_AUTORISE=oui requis (accord écrit de SIE).")
        if client is None:
            from anthropic import AnthropicBedrockMantle
            client = AnthropicBedrockMantle(aws_region=region or os.getenv("SIE_AWS_REGION", "eu-west-3"))
        # Pas de relance automatique côté serveur sur Bedrock : un refus est signalé tel quel.
        super().__init__(client, modele or os.getenv("SIE_LLM_MODELE", "anthropic.claude-sonnet-5-5"), serveur_fallback=False)
        self.nom = f"bedrock:{self.modele}"


# --- OpenAI (API Responses) ----------------------------------------------------------------
SCHEMA_BROUILLON = {
    "type": "object", "additionalProperties": False, "required": ["texte", "citations"],
    "properties": {
        "texte": {"type": "string"},
        "citations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["passage", "extrait"],
            "properties": {"passage": {"type": "integer"}, "extrait": {"type": "string"}}}},
    },
}

CONSIGNE_JSON = ("\nLes passages sont numérotés [n]. Réponds par un objet JSON : « texte » contient le brouillon, "
                 "« citations » la liste des passages utilisés, avec pour chacun son numéro et un extrait recopié mot pour mot "
                 "(8 à 30 mots) qui appuie le brouillon.")


def _passages_numerotes(passages: list[Passage]) -> str:
    return "\n".join(f"\n[{i}] {p.titre}\n{p.texte}" for i, p in enumerate(passages, 1))


def _citations_json(obj: dict, n: int) -> list[Citation]:
    return [Citation(c["passage"] - 1, str(c.get("extrait", ""))) for c in obj.get("citations") or []
            if isinstance(c, dict) and isinstance(c.get("passage"), int) and 1 <= c["passage"] <= n]


class OpenAIRedacteur:
    """API OpenAI, sortie JSON imposée par un schéma (texte + citations).

    store=False : OpenAI ne conserve pas la réponse côté application. Les journaux de contrôle des abus
    (30 jours au plus chez OpenAI) ne disparaissent qu'avec la « conservation zéro » accordée par OpenAI.
    """
    externe = True

    def __init__(self, modele: str | None = None, client: Any = None, url: str | None = None):
        if not externe_autorise():
            raise ErreurAutorisation("IA extérieure bloquée : SIE_LLM_EXTERNE_AUTORISE=oui requis (accord écrit de SIE).")
        self.modele = modele or os.getenv("SIE_LLM_MODELE", "gpt-6.2-sol")
        self.effort = os.getenv("SIE_LLM_EFFORT", "medium")
        if client is None:
            from openai import OpenAI
            client = OpenAI(base_url=url or os.getenv("SIE_OPENAI_URL") or None, timeout=600, max_retries=2)
        self.client = client
        self.nom = f"openai:{self.modele}"

    def cout(self, usage: Any) -> float:
        if usage is None:
            return 0.0
        e, s = TARIFS.get(self.modele, (2.0, 10.0))
        entree = getattr(usage, "input_tokens", 0) or 0
        details = getattr(usage, "input_tokens_details", None)
        cache = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        # Jetons lus en cache : un dixième du tarif d'entrée chez OpenAI (gpt-6.2-sol : 0,10 $ au lieu de 2 $).
        return ((entree - cache) * e + 0.1 * cache * e + (getattr(usage, "output_tokens", 0) or 0) * s) / 1_000_000

    def rediger(self, consigne: str, question: str, passages: list[Passage]) -> Reponse:
        try:
            rep = self.client.responses.create(
                model=self.modele, instructions=consigne + CONSIGNE_JSON,
                input=f"{_passages_numerotes(passages)}\n\n{question}",
                text={"format": {"type": "json_schema", "name": "brouillon", "schema": SCHEMA_BROUILLON, "strict": True}},
                reasoning={"effort": self.effort}, max_output_tokens=16000, store=False)
        except Exception as e:
            return Reponse("", erreur=f"Appel à l'IA de rédaction impossible : {type(e).__name__} {str(e)[:300]}")
        cout = self.cout(getattr(rep, "usage", None))
        for item in getattr(rep, "output", None) or []:
            for c in getattr(item, "content", None) or []:
                if getattr(c, "type", "") == "refusal":
                    return Reponse("", cout_usd=cout, erreur="L'IA de rédaction a refusé cette demande.")
        tronque = getattr(rep, "status", "") == "incomplete"
        try:
            obj = json.loads(getattr(rep, "output_text", "") or "")
        except ValueError:
            obj = None
        if not isinstance(obj, dict) or not isinstance(obj.get("texte"), str):
            raison = " (réponse interrompue, trop longue)" if tronque else ""
            return Reponse("", cout_usd=cout, erreur=f"Réponse de l'IA de rédaction inexploitable{raison}.")
        texte = obj["texte"].strip()
        if tronque:
            texte += "\n\n[À COMPLÉTER : brouillon interrompu, trop long]"
        return Reponse(texte, _citations_json(obj, len(passages)), cout_usd=cout)


# --- Modèle local (Ollama) -----------------------------------------------------------------
class OllamaRedacteur:
    externe = False

    def __init__(self, modele: str | None = None, url: str | None = None):
        self.modele = modele or os.getenv("SIE_LLM_MODELE", "mistral-small3.2")
        self.url = (url or os.getenv("SIE_OLLAMA_URL", "http://localhost:11434")).rstrip("/")
        self.nom = f"ollama:{self.modele}"

    def rediger(self, consigne: str, question: str, passages: list[Passage]) -> Reponse:
        corps = _passages_numerotes(passages)
        consigne += ("\nLes passages sont numérotés [n]. Réponds uniquement par un objet JSON : "
                     '{"texte": "brouillon", "citations": [{"passage": n, "extrait": "citation exacte de 8 à 30 mots"}]}')
        req = urllib.request.Request(f"{self.url}/api/chat", headers={"Content-Type": "application/json"}, data=json.dumps(
            {"model": self.modele, "stream": False, "format": "json", "options": {"temperature": 0.2},
             "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": f"{corps}\n\n{question}"}]}).encode())
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                contenu = json.loads(r.read()).get("message", {}).get("content", "")
        except Exception as e:
            return Reponse("", erreur=f"Modèle local injoignable ({self.url}) : {e}")
        m = re.search(r"\{.*\}", contenu, re.DOTALL)
        try:
            obj = json.loads(m.group(0)) if m else {}
        except ValueError:
            obj = {}
        if not isinstance(obj.get("texte"), str):
            return Reponse("", erreur="Réponse du modèle local inexploitable.")
        return Reponse(obj["texte"], _citations_json(obj, len(passages)))


def redacteur(choix: str | None = None) -> Redacteur:
    """« openai », « anthropic », … ou « moteur:modèle » (ex. openai:gpt-6.2-astra) pour comparer deux modèles."""
    choix = (choix or os.getenv("SIE_LLM", "aucun")).strip()
    nom, _, modele = choix.partition(":")
    nom, modele = nom.lower(), modele or None
    if nom in ("openai", "gpt"):
        return OpenAIRedacteur(modele)
    if nom == "bedrock":
        return BedrockRedacteur(modele)
    if nom in ("anthropic", "claude"):
        return AnthropicRedacteur(modele)
    if nom == "ollama":
        return OllamaRedacteur(modele)
    return AucunRedacteur()
