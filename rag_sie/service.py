"""Service HTTP interne, appelé par l'interface SIE Tender Intelligence (maquette V2 : rag.intranet.sie.local).

    GET  /health                 état du service (détails si authentifié)
    POST /sync                   lance une synchronisation (profils autorisés) ; 409 si déjà en cours
    GET  /sync/status            progression, dernier succès, nombre de fichiers, erreurs
    POST /search                 passages d'archives pour une requête, filtrés par les droits
    POST /similaires             affaires passées les plus proches d'une description
    POST /draft                  brouillon de section cité (ou passages seuls si l'IA est désactivée)
    GET  /documents/{id}         fiche d'un document autorisé (sections, métadonnées, chemin sur le partage)
    GET  /journal                derniers envois à l'IA de rédaction (profils autorisés)

Authentification : l'interface authentifie l'utilisateur (Active Directory) puis appelle le service avec
    Authorization: Bearer <SIE_RAG_JETON>        secret partagé interface ↔ service
    X-SIE-Utilisateur: SIE\\jdupont
    X-SIE-Groupes: SIE\\BE;SIE\\Direction        groupes AD de l'utilisateur
Le service n'écoute que sur le réseau interne ; HTTPS par le serveur web frontal (IIS) ou uvicorn --ssl-*.

Lancement : python -m rag_sie servir --hote 0.0.0.0 --port 8600
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import PureWindowsPath
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .droits import Utilisateur, autorise
from .entrepot import Filtre
from .moteur import VERSION, Moteur
from .redacteurs import ErreurAutorisation, externe_autorise
from .redaction import DemandeSection, rediger


class RequeteSync(BaseModel):
    forcer: bool = False
    autoriser_suppressions_massives: bool = False


class RequeteRecherche(BaseModel):
    requete: str = Field(min_length=2, max_length=2000)
    k: int = Field(8, ge=1, le=50)
    types: Optional[list[str]] = None
    valides_seulement: bool = True
    annee_min: Optional[int] = None
    activites: Optional[list[str]] = None
    type_ouvrage: Optional[str] = None
    affaires: Optional[list[str]] = None
    contexte: str = ""
    elargir: bool = False
    mode: str = Field("hybride", pattern="^(hybride|mots|sens)$")


class RequeteSimilaires(BaseModel):
    description: str = Field(min_length=2, max_length=4000)
    k: int = Field(5, ge=1, le=20)


class RequeteBrouillon(BaseModel):
    titre: str = Field(min_length=2, max_length=300)
    consigne: str = ""
    critere: Optional[str] = None
    ponderation: Optional[float] = None
    exigences: list[str] = Field(default_factory=list)
    objet_marche: str = ""
    lot: Optional[str] = None
    activites: list[str] = Field(default_factory=list)
    type_ouvrage: Optional[str] = None
    requete: Optional[str] = None
    k: int = Field(8, ge=1, le=12)


def creer_app(moteur: Moteur | None = None, planifier: bool = True) -> FastAPI:
    moteur = moteur or Moteur()
    cfg = moteur.config
    demo = os.getenv("SIE_RAG_DEMO") == "1"
    app = FastAPI(title="SIE Tender Intelligence — RAG archives", version=VERSION)
    app.state.moteur = moteur

    def utilisateur(authorization: str = Header(""), x_sie_utilisateur: str = Header(""),
                    x_sie_groupes: str = Header("")) -> Utilisateur:
        if cfg.jeton:
            if authorization != f"Bearer {cfg.jeton}":
                raise HTTPException(401, "Jeton du service absent ou invalide.")
        elif not demo:
            raise HTTPException(503, "Service non configuré : définir SIE_RAG_JETON (ou SIE_RAG_DEMO=1 pour une démonstration).")
        if not x_sie_utilisateur:
            raise HTTPException(401, "Utilisateur non transmis (en-tête X-SIE-Utilisateur).")
        groupes = [g.strip() for g in x_sie_groupes.split(";") if g.strip()]
        admins = {g.lower() for g in cfg.groupes_admin}
        est_admin = bool(admins & {g.lower() for g in groupes}) or (demo and not admins)
        return Utilisateur(x_sie_utilisateur, groupes, admin=est_admin)

    def lancer_synchro(forcer=False, massives=False) -> bool:
        if moteur.etat_synchro.verrou_actif():
            return False
        threading.Thread(target=moteur.indexeur.synchroniser, kwargs={"forcer": forcer,
                         "autoriser_suppressions_massives": massives}, daemon=True).start()
        time.sleep(0.05)
        return True

    @app.get("/health")
    def health(authorization: str = Header("")):
        base = {"statut": "ok", "version": VERSION}
        if not ((cfg.jeton and authorization == f"Bearer {cfg.jeton}") or demo):
            return base
        try:
            stats = moteur.entrepot.stats()
        except Exception as e:  # base injoignable
            return {**base, "statut": "degrade", "erreur": str(e)}
        etat = moteur.etat_synchro.lire()
        avert = []
        if stats["encodeur"].startswith("hachage"):
            avert.append("Encodeur de démonstration (hachage) : installer sentence-transformers et bge-m3 pour la production.")
        return {**base, "index": stats, "ia_redaction": os.getenv("SIE_LLM", "aucun"), "ia_externe_autorisee": externe_autorise(),
                "synchro": {k: etat.get(k) for k in ("en_cours", "dernier_succes", "nb_fichiers", "statut", "progression")},
                "avertissements": avert}

    @app.post("/sync", status_code=202)
    def sync(req: RequeteSync = RequeteSync(), u: Utilisateur = Depends(utilisateur)):
        if not u.admin:
            raise HTTPException(403, "Synchronisation réservée aux profils autorisés.")
        if not lancer_synchro(req.forcer, req.autoriser_suppressions_massives):
            raise HTTPException(409, "Une synchronisation est déjà en cours.")
        return {"lance": True, "par": u.login, "a": datetime.now().isoformat(timespec="seconds")}

    @app.get("/sync/status")
    def sync_status(u: Utilisateur = Depends(utilisateur)):
        return moteur.etat_synchro.lire()

    def filtre_de(req, u: Utilisateur) -> Filtre:
        return Filtre(utilisateur=u, types=getattr(req, "types", None), valides_seulement=getattr(req, "valides_seulement", True),
                      annee_min=getattr(req, "annee_min", None), activites=getattr(req, "activites", None),
                      type_ouvrage=getattr(req, "type_ouvrage", None), affaires=getattr(req, "affaires", None))

    @app.post("/search")
    def search(req: RequeteRecherche, u: Utilisateur = Depends(utilisateur)):
        from .recherche import Contexte

        t0 = time.time()
        res = moteur.recherche.chercher(req.requete, filtre_de(req, u), k=req.k, mode=req.mode,
                                        contexte=Contexte(req.activites or [], req.type_ouvrage, req.contexte))
        out = []
        for b in res:
            d = b.dict()
            d["chemin_partage"] = chemin_partage(cfg.archives, b.fichier)
            if req.elargir:
                d["section"] = moteur.recherche.section_complete(b)
            out.append(d)
        return {"resultats": out, "duree_ms": int((time.time() - t0) * 1000)}

    @app.post("/similaires")
    def similaires(req: RequeteSimilaires, u: Utilisateur = Depends(utilisateur)):
        return {"affaires": moteur.recherche.affaires_similaires(req.description, Filtre(utilisateur=u), req.k)}

    @app.post("/draft")
    def draft(req: RequeteBrouillon, u: Utilisateur = Depends(utilisateur)):
        try:
            red = moteur.redacteur
        except ErreurAutorisation as e:
            raise HTTPException(403, str(e))
        br = rediger(DemandeSection(**req.model_dump()), moteur.recherche, red, Filtre(utilisateur=u), moteur.journal, u.login)
        d = br.dict()
        for p in d["passages"]:
            p["chemin_partage"] = chemin_partage(cfg.archives, p["fichier"])
        return d

    @app.get("/documents/{document_id}")
    def document(document_id: int, u: Utilisateur = Depends(utilisateur)):
        doc = moteur.entrepot.document(document_id)
        if not doc or not autorise(u, doc["comptes"]):
            raise HTTPException(404, "Document introuvable.")  # même réponse : on ne révèle pas son existence
        doc.pop("comptes")
        doc["chemin_partage"] = chemin_partage(cfg.archives, doc["fichier"])
        return doc

    @app.get("/journal")
    def journal(n: int = 100, u: Utilisateur = Depends(utilisateur)):
        if not u.admin:
            raise HTTPException(403, "Journal réservé aux profils autorisés.")
        return {"envois": moteur.journal.lire(n)}

    heure = os.getenv("SIE_RAG_HEURE_SYNCHRO", "")
    if planifier and heure:
        threading.Thread(target=_planificateur, args=(heure, lancer_synchro), daemon=True).start()
    return app


def chemin_partage(racine: str, rel: str) -> str:
    """Chemin complet du fichier sur le partage, pour que l'utilisateur l'ouvre depuis son poste."""
    if not racine:
        return rel
    if racine.startswith("\\\\") or ":" in racine[:3]:
        return str(PureWindowsPath(racine, *rel.split("/")))
    return f"{racine.rstrip('/')}/{rel}"


def _planificateur(heure: str, lancer) -> None:
    """Synchronisation quotidienne à heure fixe (SIE_RAG_HEURE_SYNCHRO=02:00), sans planificateur Windows."""
    h, m = (int(x) for x in heure.split(":"))
    while True:
        maintenant = datetime.now()
        cible = maintenant.replace(hour=h, minute=m, second=0, microsecond=0)
        if cible <= maintenant:
            cible += timedelta(days=1)
        time.sleep((cible - maintenant).total_seconds())
        lancer()
