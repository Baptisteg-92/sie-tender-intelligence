"""Interface de l'outil (Streamlit), organisée comme la maquette V2 du groupe 2 et de SIE.

    streamlit run app.py

Rubriques : Tableau de bord, Consultations, Chargement du DCE, Analyse du DCE, Fiche de renseignement,
Décomptes & métrés, Demandes de prix fournisseurs, Chiffrage / DPGF, Mémoire technique, Documents,
Contrôle & export, Paramètres. Un sélecteur commun (barre latérale) choisit la consultation traitée.

Variables d'environnement utiles :
    SIE_ESPACE        dossier des affaires (défaut : affaires) ; les référentiels sont dans <espace>/_referentiels.json
    SIE_INDEX         index des archives (défaut : archives_index.json)
    SIE_RAG_URL       service RAG (rag_sie/) : s'il est défini, il remplace l'index local (voir sie_tender/rag_distant.py)
    SIE_LLM           aucun | openai | ollama | anthropic : moteur IA proposé par défaut (voir sie_tender/llm.py)
    SIE_MOT_DE_PASSE  protège l'accès (obligatoire en ligne)
"""
from __future__ import annotations

import io
import os
import tempfile
import zipfile
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from sie_tender import ia
from sie_tender.affaire import Affaire, EspaceTravail
from sie_tender.export import (LIBELLES, complements_a_demander, exporter_complements_docx, exporter_excel,
                               exporter_fiche_modele, exporter_trame_docx, ref, sections_memoire)
from sie_tender.f2_synthese import exporter_synthese_docx, heures_insertion, matrice_interfaces, syntheses_retenues, tableau_croise
from sie_tender.f3_quantites import apercu, controler_metre, feuilles, importer_quantites
from sie_tender.f4_cotations import (courriel_fournisseur, demande_fournisseur, exporter_demande, exporter_demandes,
                                     extraire_pages, generer_demandes, lire_date, lire_plages, texte_courriel)
from sie_tender.f5_dpgf import _prix_vente, associer, construire_dpgf, exporter_dpgf, importer_devis, offres_par_poste, retenir
from sie_tender.f6_archives import Bibliotheque, preparer_memoires
from sie_tender.f7_controle import VALIDATIONS_FINALES, controler, exporter_dossier, peut_declarer_pret
from sie_tender.impression import fiche_pdf, registre_pdf
from sie_tender.ingestion import importer_dce
from sie_tender.llm import client, externe_autorise, moteurs_disponibles
from sie_tender.models import (DESTINATIONS_ALERTE, STATUTS_ALERTE, STATUTS_DEMANDE, Alerte, Champ, ImportMetre, LigneQuantite,
                               Lot, PieceReponse, Statut, StatutSection, TypePiece)
from sie_tender.pipeline import analyser_affaire
from sie_tender.referentiels import ROLES, SPECIALITES, Contact, Fournisseur, PersonneVisite, Referentiels, Utilisateur, email_valide
from sie_tender.suivi import (OUVERTS, avancement, initialiser_registre, points_memoire, revalide, signaler_modification,
                              statut_metier, synchroniser_pieces, synthese_registre)
from sie_tender.synchro import synchroniser

st.set_page_config(page_title="SIE Tender Intelligence", page_icon=":material/bolt:", layout="wide")

ESPACE = EspaceTravail(os.getenv("SIE_ESPACE", "affaires"))
FICHIER_REFS = ESPACE.racine / "_referentiels.json"
INDEX = Path(os.getenv("SIE_INDEX", "archives_index.json"))
DONNEES = Path(os.getenv("SIE_DATA", "/data" if Path("/data").is_dir() else "donnees_demo"))

st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1400px;}
.sie-entete {border-left: 4px solid var(--primary-color, #1f5f99); padding: .2rem 0 .2rem .9rem; margin-bottom: 1rem;}
.sie-entete .surtitre {font-size: .72rem; letter-spacing: .08em; text-transform: uppercase; opacity: .65;}
.sie-entete h2 {margin: 0; padding: 0; font-size: 1.45rem;}
.sie-entete .sous {opacity: .75; font-size: .92rem;}
.sie-pastille {display: inline-block; padding: .08rem .55rem; border-radius: 999px; font-size: .8rem; margin-right: .35rem;
               border: 1px solid rgba(128,128,128,.35);}
.sie-ok {background: rgba(46,160,67,.15);} .sie-alerte {background: rgba(219,109,40,.18);} .sie-bloque {background: rgba(218,54,51,.18);}
.sie-carte {border: 1px solid rgba(128,128,128,.25); border-radius: .6rem; padding: .7rem 1rem; min-height: 6.2rem;}
.sie-carte .titre {font-size: .8rem; opacity: .7; text-transform: uppercase; letter-spacing: .03em;}
.sie-carte .valeur {font-size: 1.35rem; font-weight: 600; margin-top: .15rem; line-height: 1.3;}
.sie-citation {border-left: 3px solid rgba(128,128,128,.4); padding-left: .7rem; margin: .3rem 0; font-size: .9rem; opacity: .9;}
.sie-etapes {display: flex; gap: .5rem; flex-wrap: wrap; margin: .3rem 0 .6rem 0;}
.sie-etape {flex: 1; min-width: 7rem; border: 1px solid rgba(128,128,128,.25); border-radius: .5rem; padding: .45rem .7rem; font-size: .85rem;}
.sie-etape b {display: block; font-size: .95rem;}
</style>
""", unsafe_allow_html=True)


# --- Préparation ---------------------------------------------------------------------
@st.cache_resource
def preparer_demo() -> None:
    """Mode démo (SIE_DEMO=1) : données fictives et archives indexées au premier lancement."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "scripts"))
    if not (DONNEES / "exemples" / "dce_test").exists():
        from generer_dce_test import generer as generer_dce_test
        generer_dce_test(DONNEES / "exemples" / "dce_test")
    if (DONNEES / "exemples" / "archives_fictives").exists() and INDEX.exists():
        return
    from generer_donnees_fictives import generer
    generer(DONNEES / "exemples")
    Bibliotheque.indexer(DONNEES / "exemples" / "archives_fictives").sauver(INDEX)


_MDP = os.getenv("SIE_MOT_DE_PASSE")
if _MDP and not st.session_state.get("connecte"):
    import hmac

    _, centre, _ = st.columns([1, 1.2, 1])
    with centre:
        st.title(":material/bolt: SIE Tender Intelligence")
        with st.form("connexion"):
            saisie = st.text_input("Mot de passe", type="password")
            if st.form_submit_button("Entrer", type="primary", width="stretch"):
                if hmac.compare_digest(saisie.encode(), _MDP.encode()):
                    st.session_state["connecte"] = True
                    st.rerun()
                st.error("Mot de passe incorrect.")
    st.stop()
if os.getenv("SIE_DEMO") == "1" or os.getenv("SIE_EXEMPLES") == "1":
    preparer_demo()


# --- Données en cache (rechargées seulement quand le fichier change) ---------------------
def _mtime(p: Path) -> float:
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_resource
def _bibliotheque(chemin: str, mtime: float) -> Bibliotheque | None:
    return Bibliotheque.charger(chemin) if Path(chemin).exists() else None


def biblio() -> Bibliotheque | None:
    from sie_tender.rag_distant import BibliothequeRAG

    distante = BibliothequeRAG.depuis_env()
    if distante is not None:
        return distante  # type: ignore[return-value]  (mêmes méthodes que Bibliotheque)
    return _bibliotheque(str(INDEX), _mtime(INDEX))


@st.cache_resource(max_entries=20)
def _affaire(aid: str, mtime: float) -> Affaire:
    return ESPACE.ouvrir(aid)


def ouvrir(aid: str) -> Affaire:
    return _affaire(aid, _mtime(ESPACE.racine / aid / "consultation.json"))


def refs() -> Referentiels:
    return Referentiels.charger(FICHIER_REFS)


def sauver_refs(r: Referentiels) -> None:
    r.sauver(FICHIER_REFS)


@st.cache_data(max_entries=200, show_spinner=False)
def _resume(aid: str, mtime: float) -> dict:
    c = ESPACE.ouvrir(aid).c
    limite = lire_date(c.fiche.date_limite_remise.valeur)
    reg = synthese_registre(c)
    total = sum(p.total or 0 for p in c.dpgf)
    return {"id": aid, "titre": c.fiche.intitule.valeur or c.fiche.objet.valeur or "(DCE non analysé)",
            "reference": c.fiche.reference.valeur or "", "acheteur": c.fiche.maitre_ouvrage.valeur or "",
            "limite": limite, "decision": c.fiche.decision or "", "lots": len(c.lots),
            "retenus": ", ".join(l.numero for l in c.lots if l.retenu), "statut": statut_metier(c),
            "responsable": c.responsable or "", "archivee": c.archivee, "avancement": avancement(c),
            "alertes": reg["ouverts"], "bloquants": reg["bloquants"],
            "devis_attendus": sum(d.statut in ("envoyé par SIE", "relancé") for d in c.demandes_cotation),
            "sans_prix": sum(p.prix_unitaire_vente is None for p in c.dpgf),
            "memoire_ouvert": bool(c.trame_memoire) and not any(v.etape == "memoire" for v in c.validations),
            "pieces_manquantes": sum(not p.recu for p in c.pieces_reponse if p.obligatoire == "Obligatoire"),
            "montant": total or None, "revalider": list(c.a_revalider),
            "etapes": sum(any(v.etape == e for v in c.validations) for e in ("fiche", "quantites", "dpgf", "memoire")),
            "cree": c.date_analyse}


def resumes(archivees: bool = False) -> list[dict]:
    out = []
    for rep in sorted(ESPACE.racine.iterdir(), reverse=True):
        f = rep / "consultation.json"
        if rep.is_dir() and f.exists():
            r = _resume(rep.name, _mtime(f))
            if archivees or not r["archivee"]:
                out.append(r)
    return out


@st.cache_resource(max_entries=5, show_spinner="Lecture des pièces du DCE…")
def _pieces(aid: str, mtime: float):
    return importer_dce(ESPACE.racine / aid / "dce")


def pieces_texte(a: Affaire):
    """Texte des pièces (non stocké dans consultation.json) : relu une fois puis gardé en mémoire."""
    d = a.dossier("dce")
    return _pieces(a.id, max([_mtime(f) for f in d.iterdir()] + [0.0]))


@st.cache_data(max_entries=30, show_spinner=False)
def _export(_a: Affaire, nom: str, mtime: float) -> bytes:
    with tempfile.TemporaryDirectory() as t:
        chemin = Path(t) / nom
        if nom.startswith("Memoire_technique_lot"):
            exporter_trame_docx(_a.c, chemin, nom.removeprefix("Memoire_technique_lot").removesuffix(".docx"))
            return chemin.read_bytes()
        {"analyse_dce.xlsx": lambda: exporter_excel(_a.c, chemin),
         "Memoire_technique.docx": lambda: exporter_trame_docx(_a.c, chemin),
         "Synthese_lots_retenus.docx": lambda: exporter_synthese_docx(_a.c, pieces_texte(_a), chemin),
         "Complements_memoire.docx": lambda: exporter_complements_docx(_a.c, chemin),
         "Fiche_renseignement.xlsx": lambda: exporter_fiche_modele(_a.c, _a.dossier("exports") / "modele_fiche.xlsx", chemin),
         "Fiche_renseignement.pdf": lambda: fiche_pdf(_a.c, chemin),
         "Registre_vigilance.pdf": lambda: registre_pdf(_a.c, chemin),
         "demandes_cotation.xlsx": lambda: exporter_demandes(_a.c, chemin),
         "DPGF.xlsx": lambda: exporter_dpgf(_a.c, chemin, _a.dossier("exports") / "modele_dpgf.xlsx"
                                            if (_a.dossier("exports") / "modele_dpgf.xlsx").exists() else None)}[nom]()
        return chemin.read_bytes()


def bouton_export(a: Affaire, label: str, nom: str, cle: str = "") -> None:
    modeles = [_mtime(f) for f in a.dossier("exports").glob("modele_*.xlsx")]
    donnees = _export(a, nom, max([_mtime(a.racine / "consultation.json")] + modeles))
    st.download_button(label, donnees, file_name=nom, icon=":material/download:", key=f"dl_{nom}_{cle}")


# --- Outils d'affichage -----------------------------------------------------------------
def utilisateur() -> str:
    return st.session_state.get("utilisateur") or "?"


def role() -> str:
    return st.session_state.get("role") or ROLES[1]


def maintenant() -> str:
    return f"{datetime.now():%d/%m/%Y %H:%M}"


def historique(a: Affaire) -> None:
    if not a.c.validations:
        st.caption("Aucune validation enregistrée.")
        return
    st.dataframe(pd.DataFrame([{"Date": v.date, "Étape": v.etape, "Par": v.valideur, "Rôle": v.role or "", "Commentaire": v.commentaire or ""}
                               for v in reversed(a.c.validations)]), hide_index=True, width="stretch",
                 column_config={"Date": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})


def jours_restants(limite: date | None) -> str:
    if not limite:
        return "date à vérifier"
    n = (limite - date.today()).days
    return "dépassée" if n < 0 else ("aujourd'hui" if n == 0 else f"J-{n}")


def pastille(texte: str, genre: str = "") -> str:
    return f'<span class="sie-pastille {genre}">{texte}</span>'


def carte(col, titre: str, valeur: str, aide: str = "") -> None:
    col.markdown(f'<div class="sie-carte"><div class="titre">{titre}</div><div class="valeur">{valeur}</div>'
                 f'<div class="sous" style="font-size:.8rem;opacity:.7">{aide}</div></div>', unsafe_allow_html=True)


def entete(titre: str, sous_titre: str = "", a: Affaire | None = None) -> None:
    infos = ""
    if a is not None:
        c = a.c
        limite = lire_date(c.fiche.date_limite_remise.valeur)
        j = jours_restants(limite)
        genre = "sie-bloque" if j in ("dépassée", "aujourd'hui") or (j.startswith("J-") and int(j[2:]) <= 7) else ""
        lots = ", ".join(l.numero for l in c.lots if l.retenu)
        infos = (pastille(f"Dossier actif : {c.fiche.reference.valeur or a.id}", "sie-ok") + pastille(f"Remise : {j}", genre)
                 + pastille(f"Décision : {c.fiche.decision or 'en attente'}",
                            "sie-ok" if c.fiche.decision == "GO" else ("sie-bloque" if c.fiche.decision else "sie-alerte"))
                 + (pastille(f"Lots retenus : {lots}") if lots else "") + pastille(statut_metier(c)))
        sous_titre = (c.fiche.intitule.valeur or c.fiche.objet.valeur or a.id) + (f" · {sous_titre}" if sous_titre else "")
    st.markdown(f'<div class="sie-entete"><div class="surtitre">SIE / Tender Intelligence</div><h2>{titre}</h2>'
                f'<div class="sous">{sous_titre}</div><div style="margin-top:.35rem">{infos}</div></div>', unsafe_allow_html=True)
    if a is not None and a.c.a_revalider:
        st.warning("Une donnée amont validée a changé. À revoir et revalider : " + ", ".join(a.c.a_revalider) + ".",
                   icon=":material/sync_problem:")


def affaire_courante() -> Affaire:
    aid = st.session_state.get("affaire")
    if not aid or not (ESPACE.racine / aid / "consultation.json").exists():
        st.info("Choisis une consultation dans le sélecteur de la barre latérale, ou charge un nouveau DCE.", icon=":material/folder_open:")
        st.page_link(PAGES["chargement"], label="Charger un DCE", icon=":material/upload:")
        st.stop()
    return ouvrir(aid)


def aller(cle: str, aid: str | None = None) -> None:
    if aid:
        st.session_state["affaire"] = aid
    st.switch_page(PAGES[cle])


def suivant(cle: str, label: str) -> None:
    st.divider()
    if st.button(f"Étape suivante : {label}", icon=":material/arrow_forward:", key=f"suiv_{cle}"):
        aller(cle)


def noms_personnel(r: Referentiels) -> list[str]:
    return [u.libelle for u in r.utilisateurs if u.actif]


CATEGORIES = {"RSE": "RSE", "eliminatoire": "Éliminatoire", "interface": "Interfaces", "planning": "Planning", "penalite": "Pénalités",
              "site_occupe": "Site occupé", "technique": "Technique", "administrative": "Administratif"}

STATUT_AFFICHE = {Statut.EXTRAIT: "✅ extrait", Statut.A_VERIFIER: "⚠️ à vérifier", Statut.CORRIGE: "✏️ corrigé",
                  Statut.VALIDE: "✔️ validé"}
TRI = {"À vérifier": None, "Oui": "oui", "Non": "non"}
TRI_INV = {None: "À vérifier", "oui": "Oui", "non": "Non"}


def deposer_dce(a: Affaire, fichiers: list[tuple[str, bytes]]) -> int:
    n = 0
    for nom, contenu in fichiers:
        if nom.lower().endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(contenu)) as z:
                for info in z.infolist():
                    if not info.is_dir() and not Path(info.filename).name.startswith((".", "__")):
                        a.deposer("dce", Path(info.filename).name, z.read(info))
                        n += 1
        else:
            a.deposer("dce", nom, contenu)
            n += 1
    return n


def analyser(a: Affaire) -> None:
    with st.status("Analyse du DCE…", expanded=False) as s:
        s.write("Lecture des pièces, fiche, exigences, critères, recherche dans les archives")
        analyser_affaire(a, biblio())
        initialiser_registre(a.c)
        synchroniser_pieces(a.c)
        a.sauver()
        s.update(label="Analyse terminée", state="complete")


def creer_affaire(fichiers: list[tuple[str, bytes]]) -> None:
    a = ESPACE.creer()
    deposer_dce(a, fichiers)
    analyser(a)
    st.session_state["affaire"] = a.id
    st.toast(f"Consultation créée : {a.c.fiche.intitule.valeur or a.id}", icon=":material/check_circle:")
    st.switch_page(PAGES["fiche"])


# --- IA -----------------------------------------------------------------------------------
NOMS_MOTEURS = {"aucun": "Sans IA (règles seules)", "openai": "OpenAI (API, accord SIE)", "anthropic": "Claude (API, accord SIE)", "ollama": "Modèle local (Ollama)"}


@st.cache_data(ttl=60, show_spinner=False)
def _moteurs() -> list[str]:
    return moteurs_disponibles()


def moteur_choisi() -> str:
    dispo = _moteurs()
    choix = st.session_state.get("moteur") or os.getenv("SIE_LLM", "aucun").lower()
    return choix if choix in dispo else "aucun"


def moteur_pour(a: Affaire):
    """Moteur IA utilisable pour cette affaire, ou None (avec explication affichée).

    SIE a validé l'usage d'un LLM par API : l'administrateur l'active sur le serveur (SIE_LLM_EXTERNE_AUTORISE=oui
    et clé d'API). Les pages utiles du DCE sont alors envoyées à l'API ; rien n'est envoyé tant que ce n'est pas configuré.
    """
    choix = moteur_choisi()
    if choix == "aucun":
        st.info("L'IA est désactivée. Les règles d'extraction fonctionnent sans elle. Pour l'activer, voir « Configurer l'IA » "
                "dans la barre latérale.", icon=":material/info:")
        return None
    if choix in ("anthropic", "openai") and not externe_autorise():
        st.warning("API non autorisée sur ce serveur (SIE_LLM_EXTERNE_AUTORISE).", icon=":material/shield:")
        return None
    try:
        return client(choix)
    except Exception as e:
        st.error(f"IA indisponible : {e}")
        return None


def appeler_ia(a: Affaire, fonction, *args):
    with st.spinner("L'IA travaille… (de quelques secondes à une minute)"):
        try:
            r = fonction(*args)
        except RuntimeError as e:
            st.error(str(e))
            return None
    a.c.reponses_ia.append(r)
    a.c.journal.append(f"IA ({r.moteur}) : {r.question[:80]} · coût estimé {r.cout_usd:.3f} $")
    st.session_state["cout_ia"] = st.session_state.get("cout_ia", 0.0) + r.cout_usd
    a.sauver()
    return r


def afficher_reponse(a: Affaire, r, supprimable: bool = True) -> None:
    st.markdown(r.texte)
    verifiees = [c for c in r.citations if c.verifiee]
    douteuses = [c for c in r.citations if not c.verifiee]
    if r.type != "memoire":
        if not r.citations:
            st.warning("Aucune citation : réponse à vérifier dans le DCE.", icon=":material/warning:")
        elif douteuses:
            st.warning(f"{len(douteuses)} citation(s) introuvable(s) mot pour mot dans le DCE : à vérifier.", icon=":material/warning:")
    with st.expander(f"Sources ({len(verifiees)} vérifiées{f', {len(douteuses)} à vérifier' if douteuses else ''})"):
        for c in r.citations:
            marque = "✅" if c.verifiee else "⚠️"
            st.markdown(f'{marque} **{c.fichier}{f" p.{c.page}" if c.page else ""}**<div class="sie-citation">{c.extrait}</div>',
                        unsafe_allow_html=True)
    pied = st.columns([4, 1])
    pied[0].caption(f"{r.moteur} · {r.date:%d/%m/%Y %H:%M} · coût estimé {r.cout_usd:.3f} $")
    if supprimable and pied[1].button("Supprimer", key=f"del_{r.id}", icon=":material/delete:"):
        a.c.reponses_ia = [x for x in a.c.reponses_ia if x.id != r.id]
        a.sauver()
        st.rerun()


# --- Tableau de bord ---------------------------------------------------------------------------
def page_tableau() -> None:
    entete("Vue d'ensemble", "Un seul espace pour analyser, chiffrer et préparer les réponses aux appels d'offres")
    if os.getenv("SIE_DEMO") == "1":
        st.warning("Version de démonstration : n'y déposez aucun document SIE, uniquement des données fictives.", icon=":material/science:")
    liste = resumes()
    proches = [x for x in liste if x["limite"] and 0 <= (x["limite"] - date.today()).days <= 15]
    k = st.columns(6)
    carte(k[0], "Consultations actives", str(len(liste)))
    carte(k[1], "Échéances ≤ 15 jours", str(len(proches)))
    carte(k[2], "Décisions en attente", str(sum(not x["decision"] for x in liste)))
    carte(k[3], "Alertes ouvertes", str(sum(x["alertes"] for x in liste)), f"dont {sum(x['bloquants'] for x in liste)} bloquante(s)")
    carte(k[4], "Devis attendus", str(sum(x["devis_attendus"] for x in liste)))
    carte(k[5], "DPGF / mémoires à finaliser", str(sum(bool(x["sans_prix"]) + x["memoire_ouvert"] for x in liste)))
    st.write("")
    gauche, droite = st.columns([3, 2], gap="large")
    with gauche:
        st.subheader("Consultations et offres")
        if not liste:
            st.info("Aucune consultation. Charge un premier DCE.")
            st.page_link(PAGES["chargement"], label="Chargement du DCE", icon=":material/upload:")
        else:
            df = pd.DataFrame([{"id": x["id"], "Affaire": x["reference"] or x["id"], "Intitulé": x["titre"], "Statut": x["statut"],
                                "Échéance": x["limite"], "Délai": jours_restants(x["limite"]), "Avancement": x["avancement"]}
                               for x in liste])
            sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="tb_aff",
                               column_config={"id": None, "Intitulé": st.column_config.TextColumn(width="large"),
                                              "Échéance": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                              "Avancement": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})
            if sel and sel.selection.rows:
                aller("consultations", liste[sel.selection.rows[0]]["id"])
            st.caption("Clique sur une affaire pour afficher ses détails.")
    with droite:
        st.subheader("À traiter en priorité")
        actions = []
        for x in liste:
            nom = x["reference"] or x["titre"][:40]
            if not x["decision"]:
                actions.append((0, f"{nom} : décision GO / NO GO en attente", "fiche", x["id"]))
            if x["bloquants"]:
                actions.append((0, f"{nom} : {x['bloquants']} alerte(s) bloquante(s)", "analyse", x["id"]))
            if x["revalider"]:
                actions.append((1, f"{nom} : à revalider ({', '.join(x['revalider'])})", "controle", x["id"]))
            if x["sans_prix"]:
                actions.append((1, f"{nom} : {x['sans_prix']} poste(s) sans prix dans la DPGF", "dpgf", x["id"]))
            if x["pieces_manquantes"]:
                actions.append((1, f"{nom} : {x['pieces_manquantes']} pièce(s) obligatoire(s) non reçue(s)", "documents", x["id"]))
            if x["devis_attendus"]:
                actions.append((2, f"{nom} : {x['devis_attendus']} devis attendu(s)", "demandes", x["id"]))
        if not actions:
            st.caption("Rien d'urgent.")
        for i, (_, texte, page, aid) in enumerate(sorted(actions)[:10]):
            c1, c2 = st.columns([4, 1])
            c1.markdown(texte)
            if c2.button("Ouvrir", key=f"act_{i}"):
                aller(page, aid)
    st.subheader("Parcours d'une réponse")
    etapes = [("1", "DCE", "Chargement, fiche, GO / NO GO"), ("2", "Analyse", "Exigences, alertes, interfaces"),
              ("3", "Quantités", "Bureau d'études"), ("4", "Devis", "Demandes de prix, comparaison"),
              ("5", "DPGF", "Chiffrage SIE"), ("6", "Mémoire", "Commun ou par lot"), ("7", "Export", "Contrôle final, dépôt manuel")]
    st.markdown('<div class="sie-etapes">' + "".join(f'<div class="sie-etape">{n} · <b>{t}</b>{d}</div>' for n, t, d in etapes)
                + "</div>", unsafe_allow_html=True)


# --- Consultations -------------------------------------------------------------------------
def page_consultations() -> None:
    entete("Consultations", "Une vue par dossier, de la décision GO / NO GO jusqu'à l'export")
    onglets = st.tabs(["Liste des affaires", "Démonstration"])
    with onglets[0]:
        f1, f2, f3 = st.columns([3, 2, 1])
        filtre = f1.text_input("Rechercher", placeholder="intitulé, référence, acheteur, responsable…", label_visibility="collapsed")
        avec_archives = f3.toggle("Archivées", value=False)
        liste = resumes(archivees=avec_archives)
        statuts = sorted({x["statut"] for x in liste})
        choix_statuts = f2.multiselect("Statut", statuts, placeholder="Tous les statuts", label_visibility="collapsed")
        vues = [x for x in liste if (not filtre or filtre.lower() in f"{x['titre']} {x['reference']} {x['acheteur']} {x['responsable']}".lower())
                and (not choix_statuts or x["statut"] in choix_statuts)]
        if not vues:
            st.info("Aucune consultation ne correspond.")
        else:
            df = pd.DataFrame([{"id": x["id"], "Affaire": x["reference"] or x["id"], "Intitulé": x["titre"],
                                "Acheteur": x["acheteur"].split(",")[0], "Statut": x["statut"], "Échéance": x["limite"],
                                "Lots": x["lots"], "Retenus": x["retenus"], "Responsable": x["responsable"],
                                "Avancement": x["avancement"]} for x in vues])
            sel = st.dataframe(df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="table_affaires",
                               column_config={"id": None, "Intitulé": st.column_config.TextColumn(width="large"),
                                              "Échéance": st.column_config.DateColumn(format="DD/MM/YYYY"),
                                              "Avancement": st.column_config.ProgressColumn(min_value=0, max_value=1, format="percent")})
            lignes = sel.selection.rows if sel else []
            aid = vues[lignes[0]]["id"] if lignes else st.session_state.get("affaire")
            if aid and any(x["id"] == aid for x in vues):
                detail_consultation(ouvrir(aid))
            else:
                st.caption("Clique sur une ligne pour afficher la consultation.")
    with onglets[1]:
        demonstration()


def detail_consultation(a: Affaire) -> None:
    c = a.c
    r = refs()
    with st.container(border=True):
        st.markdown(f"**{c.fiche.reference.valeur or a.id} · {c.fiche.intitule.valeur or c.fiche.objet.valeur or '(DCE non analysé)'}**")
        st.caption(f"{c.fiche.maitre_ouvrage.valeur or 'Acheteur à vérifier'} · "
                   + (", ".join(f"Lot {l.numero} {l.intitule}" for l in c.lots if l.retenu) or f"{len(c.lots)} lot(s)"))
        k = st.columns(4)
        carte(k[0], "Date limite", c.fiche.date_limite_remise.valeur or "à vérifier", jours_restants(lire_date(c.fiche.date_limite_remise.valeur)))
        carte(k[1], "Statut", statut_metier(c))
        carte(k[2], "GO / NO GO", c.fiche.decision or "en attente", f"{c.fiche.decideur or ''}")
        total = sum(p.total or 0 for p in c.dpgf)
        carte(k[3], "Montant DPGF HT", f"{total:,.0f} €".replace(",", " ") if total else "—", "calculé depuis la DPGF")
        st.write("")
        c1, c2, c3 = st.columns([2, 2, 2])
        personnel = noms_personnel(r)
        options = [""] + sorted(set(personnel + ([c.responsable] if c.responsable else [])))
        resp = c1.selectbox("Responsable de l'affaire", options, index=options.index(c.responsable or ""),
                            help="Liste issue de Paramètres > Utilisateurs.", key=f"resp_{a.id}")
        if resp != (c.responsable or ""):
            c.responsable = resp or None
            a.sauver()
            st.rerun()
        with c2:
            st.write("")
            if st.button("Désarchiver" if c.archivee else "Archiver", icon=":material/inventory_2:", key=f"arch_{a.id}"):
                c.archivee = not c.archivee
                c.journal.append(f"{'Archivée' if c.archivee else 'Désarchivée'} par {utilisateur()} le {maintenant()}.")
                a.sauver()
                st.rerun()
        with c3:
            st.write("")
            with st.popover("Supprimer", icon=":material/delete:"):
                st.write("Supprimer définitivement cette consultation et ses fichiers ? Préférer l'archivage.")
                if st.button("Confirmer la suppression", type="primary", key=f"supp_{a.id}"):
                    ESPACE.supprimer(a.id)
                    if st.session_state.get("affaire") == a.id:
                        st.session_state.pop("affaire")
                    st.rerun()
        st.markdown("**Accès aux étapes**")
        b = st.columns(6)
        for col, (cle, lib) in zip(b, [("fiche", "Fiche GO / NO GO"), ("analyse", "Analyse du DCE"), ("metres", "Métrés"),
                                        ("dpgf", "Chiffrage / DPGF"), ("memoire", "Mémoire"), ("controle", "Contrôle & export")]):
            if col.button(lib, key=f"go_{cle}_{a.id}", width="stretch"):
                aller(cle, a.id)


def demonstration() -> None:
    exemples = DONNEES / "exemples"
    if not exemples.exists():
        st.info("Données fictives absentes : lancer avec SIE_DEMO=1 ou SIE_EXEMPLES=1.")
        return
    st.write("Données entièrement inventées pour essayer l'outil de bout en bout.")
    dce_demo = {"École de Valbourg, 4 lots (avec archives, métré et devis)": exemples / "dce_fictif_valbourg"}
    for z in sorted((exemples / "dce_test").glob("*.zip")):
        dce_demo[{"dce_college_lot_unique": "Collège, lot unique", "dce_hopital_3_lots": "Hôpital, 3 lots",
                  "dce_bailleur_incomplet": "Bailleur social, DCE incomplet"}.get(z.stem, z.stem)] = z
    choix = st.radio("DCE fictif", list(dce_demo), label_visibility="collapsed")
    if st.button("Créer cette affaire de démonstration", type="primary", icon=":material/science:"):
        src = dce_demo[choix]
        fichiers = [(src.name, src.read_bytes())] if src.is_file() else [(f.name, f.read_bytes()) for f in src.iterdir()]
        creer_affaire(fichiers)
    st.divider()
    c1, c2 = st.columns(2)
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as z:
        for f in sorted((exemples / "chiffrage_fictif").rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(exemples))
    c1.download_button("Métré et devis fictifs", tampon.getvalue(), file_name="chiffrage_fictif.zip", icon=":material/download:")
    if (exemples / "dce_test").exists():
        with c2.popover("Télécharger les DCE fictifs", icon=":material/download:"):
            for z in sorted((exemples / "dce_test").glob("*.zip")):
                st.download_button(z.name, z.read_bytes(), file_name=z.name, key=f"dce_{z.name}")


# --- Chargement du DCE -----------------------------------------------------------------------
def page_chargement() -> None:
    entete("Chargement du DCE complet", "Importer le dossier de consultation déjà téléchargé par SIE")
    st.info("L'outil ne télécharge pas le DCE depuis la plateforme de marchés publics : SIE le récupère elle-même, puis le "
            "dépose ici (fichiers ou ZIP), ou indique le dossier où il a été enregistré sur le serveur.", icon=":material/info:")
    aid = st.session_state.get("affaire")
    cible = st.radio("Consultation concernée", ["Nouvelle consultation", "Compléter la consultation active"],
                     index=1 if aid else 0, horizontal=True, disabled=not aid)
    onglets = st.tabs(["Depuis ce poste", "Depuis un dossier du serveur SIE"])
    fichiers: list[tuple[str, bytes]] = []
    with onglets[0]:
        envois = st.file_uploader("Pièces du DCE (PDF, Word, Excel, plans, ZIP)", accept_multiple_files=True)
        st.caption("Taille maximale par envoi fixée dans .streamlit/config.toml (maxUploadSize). Pour un DCE de plusieurs Go, "
                   "utiliser l'onglet « Depuis un dossier du serveur ».")
        if envois:
            fichiers = [(f.name, f.getvalue()) for f in envois]
    with onglets[1]:
        chemin = st.text_input("Dossier du DCE sur le serveur", placeholder=r"\\serveur\appels_offres\2026\DCE_xxx  ou  /srv/sie/dce/xxx")
        if chemin:
            p = Path(chemin)
            if p.is_dir():
                liste = [f for f in p.rglob("*") if f.is_file() and not f.name.startswith(".")]
                st.caption(f"{len(liste)} fichier(s), {sum(f.stat().st_size for f in liste) / 1e6:.1f} Mo.")
                fichiers = [(f.name, f.read_bytes()) for f in liste] if st.checkbox("Importer ces fichiers") else []
            else:
                st.warning("Dossier introuvable ou non accessible au compte de service de l'outil.")
    if fichiers and st.button("Importer et analyser", type="primary", icon=":material/play_arrow:"):
        if cible == "Nouvelle consultation":
            creer_affaire(fichiers)
        else:
            a = ouvrir(aid)
            n = deposer_dce(a, fichiers)
            st.toast(f"{n} fichier(s) ajouté(s).")
            st.session_state["reanalyser"] = True
            st.rerun()
    if not aid or not (ESPACE.racine / aid / "consultation.json").exists():
        return
    a = ouvrir(aid)
    st.subheader(f"DCE associé à {a.c.fiche.reference.valeur or a.id}")
    deposes = sorted(f.name for f in a.dossier("dce").iterdir())
    lus = {p.fichier: p for p in a.c.pieces}
    if not deposes:
        st.caption("Aucun fichier déposé.")
    else:
        METHODES = {"regle": "règles", "llm": "IA", "saisie": "corrigé à la main"}
        tableau = pd.DataFrame([{"Fichier": n, "Type": lus[n].type.value if n in lus else "AUTRE",
                                 "Classé par": METHODES.get(lus[n].type_methode, "") if n in lus else "(non analysé)",
                                 "Lot": (lus[n].lot or "commun") if n in lus else "", "Pages": lus[n].nb_pages if n in lus else None,
                                 "Lu": ("oui" if lus[n].lisible else "non (plan)") if n in lus else "",
                                 "Remarque": (lus[n].remarque or "") if n in lus else ""} for n in deposes])
        edite = st.data_editor(tableau, hide_index=True, width="stretch", key=f"types_{a.id}",
                               disabled=[col for col in tableau.columns if col != "Type"],
                               column_config={"Type": st.column_config.SelectboxColumn("Type", options=[t.value for t in TypePiece],
                                                                                       help="Corriger le type si le classement est faux")})
        changes = {r["Fichier"]: r["Type"] for _, r in edite.iterrows()
                   if r["Fichier"] in lus and r["Type"] != lus[r["Fichier"]].type.value}
        if changes:
            for fichier, t in changes.items():
                a.c.types_manuels[fichier] = t
                lus[fichier].type, lus[fichier].type_methode = TypePiece(t), "saisie"
            a.sauver()
            st.info("Type corrigé et gardé pour les prochaines analyses. Relance l'analyse pour mettre à jour la fiche et les exigences.")
    nouveaux = [n for n in deposes if n not in lus]
    if nouveaux or st.session_state.pop("reanalyser", False):
        st.warning(f"{len(nouveaux)} fichier(s) pas encore analysé(s). Relancer l'analyse remplace la fiche, les lots et les exigences "
                   "extraits (les corrections manuelles de la fiche seront à refaire) ; métrés, devis et DPGF sont conservés.")
    if deposes and st.button("Relancer l'analyse du DCE", icon=":material/refresh:"):
        analyser(a)
        st.rerun()
    c1, c2 = st.columns(2)
    if a.c.pieces:
        with c1:
            bouton_export(a, "Générer PDF / imprimer la fiche", "Fiche_renseignement.pdf", "chg")
        if c2.button("Préparer la fiche de renseignement", icon=":material/arrow_forward:"):
            aller("fiche")


# --- Fiche de renseignement ------------------------------------------------------------------
SECTIONS_FICHE = [
    ("1. Identification de l'appel d'offres", ["intitule", "reference", "objet", "procedure"]),
    ("2. Localisation du projet", ["lieu"]),
    ("3. Dates importantes", ["date_limite_remise", "date_limite_questions", "visite", "contacts", "duree_validite_offres"]),
    ("4. Acteurs du projet", ["maitre_ouvrage", "maitre_oeuvre", "architecte", "coordonnateur_sps", "coordinateur_ssi", "intervenants"]),
]
TRI_ETATS = ["visite_obligatoire", "visite_sur_rdv", "fiche_visite_signee"]


def page_fiche() -> None:
    a = affaire_courante()
    c = a.c
    f = c.fiche
    r = refs()
    entete("Fiche de renseignement · GO / NO GO", "F1 : identification, dates, intervenants, tous les lots et décision", a=a)
    a_verif = sum(ch.statut == Statut.A_VERIFIER for ch in f.champs().values())
    st.caption(f"Structure du modèle de fiche SIE. {a_verif} champ(s) à vérifier. Toute information absente reste « À vérifier » : "
               "l'outil n'invente rien. Survole un champ pour voir sa source dans le DCE.")
    go = f.decision == "GO"
    direction = role() == "Direction"
    with st.form("fiche"):
        saisies: dict[str, str] = {}
        for titre, cles in SECTIONS_FICHE:
            st.markdown(f"**{titre}**")
            cols = st.columns(3)
            for i, k in enumerate(cles):
                ch: Champ = getattr(f, k)
                aide = (f"Source : {ref(ch.source)} · « {ch.source.extrait[:200]} »" if ch.source else "Non trouvé dans le DCE.")
                aide += f" · {STATUT_AFFICHE[ch.statut]}"
                saisies[k] = cols[i % 3].text_input(LIBELLES.get(k, k), ch.valeur or "", placeholder="À vérifier", help=aide)
            if titre.startswith("3."):
                t = st.columns(3)
                for col, k in zip(t, TRI_ETATS):
                    ch = getattr(f, k)
                    saisies[k] = col.selectbox(LIBELLES.get(k, k), list(TRI), index=list(TRI).index(TRI_INV.get(ch.valeur, "À vérifier")),
                                               help=f"Source : {ref(ch.source)}" if ch.source else "À confirmer dans le RC.")
        st.markdown("**5. Décomposition en lots : liste complète**")
        st.caption("Reproduire les numéros et intitulés exacts de l'acheteur, y compris les lots non visés (ajout de ligne possible). "
                   + ("Coche les lots auxquels SIE répond." if go else "La sélection des lots est verrouillée tant que la décision GO n'est pas prise."))
        lots_df = st.data_editor(pd.DataFrame([{"N°": l.numero, "Intitulé exact": l.intitule, "Activités": ", ".join(l.activites),
                                                "Répondre": bool(l.retenu), "Source": ref(l.source)} for l in c.lots],
                                              columns=["N°", "Intitulé exact", "Activités", "Répondre", "Source"]),
                                 disabled=["Activités", "Source"] + ([] if go else ["Répondre"]), hide_index=True, num_rows="dynamic",
                                 width="stretch", key=f"lots_{a.id}_{go}",
                                 column_config={"N°": st.column_config.TextColumn(width="small"),
                                                "Répondre": st.column_config.CheckboxColumn(width="small"),
                                                "Intitulé exact": st.column_config.TextColumn(width="large")})
        st.markdown("**6. Décision GO / NO GO de la direction**")
        d1, d2 = st.columns([2, 3])
        options = ["En attente", "GO", "NO GO"]
        decision = d1.radio("Décision", options, index=options.index(f.decision or "En attente"), horizontal=True, disabled=not direction,
                            label_visibility="collapsed")
        d2.caption(("Décision réservée au rôle « Direction » (barre latérale). " if not direction else "")
                   + (f"Décision actuelle : {f.decision} par {f.decideur} le {f.date_decision:%d/%m/%Y %H:%M}." if f.date_decision
                      else "Aucune décision automatique.")
                   + (" Passer de NO GO à GO rouvre explicitement la consultation." if f.decision == "NO GO" else ""))
        st.markdown("**7. Responsable de visite et points de vigilance**")
        v1, v2 = st.columns(2)
        visiteurs = [p.libelle for p in r.visites if p.actif]
        opts = [""] + sorted(set(visiteurs + ([f.responsable_visite] if f.responsable_visite else [])))
        resp = v1.selectbox("Responsable de la visite", opts, index=opts.index(f.responsable_visite or ""), disabled=not go,
                            help="Personnes de Paramètres > Personnes pour visites de site. Désigné après GO.")
        date_v = v2.date_input("Date de la visite", value=lire_date(f.date_visite) if f.date_visite else None, format="DD/MM/YYYY",
                               disabled=not go)
        commentaires = st.text_area("Commentaires / validations SIE", f.commentaires or "", placeholder="Observations de la direction, points à vérifier…")
        envoye = st.form_submit_button("Enregistrer la fiche", type="primary", icon=":material/save:")
    if envoye:
        modifie = False
        for k, v in saisies.items():
            ch: Champ = getattr(f, k)
            v = TRI[v] if k in TRI_ETATS else (v.strip() or None)
            if v != ch.valeur:
                ch.valeur, ch.statut, ch.methode = v, (Statut.CORRIGE if v else Statut.A_VERIFIER), "saisie"
                modifie = True
        anciens = {l.numero: l for l in c.lots}
        nouveaux = []
        for _, x in lots_df.iterrows():
            num = str(x["N°"] or "").strip()
            if not num:
                continue
            l = anciens.get(num) or Lot(numero=num, intitule="")
            l.intitule = str(x["Intitulé exact"] or "").strip() or "(intitulé à vérifier)"
            if go:
                l.retenu = bool(x["Répondre"])
            nouveaux.append(l)
        if [(l.numero, l.retenu) for l in nouveaux] != [(l.numero, l.retenu) for l in c.lots]:
            modifie = True
        c.lots = nouveaux
        dec = None if decision == "En attente" else decision
        if direction and dec != f.decision:
            if dec is None:
                st.error("Une décision prise ne peut pas revenir « en attente » : choisis GO ou NO GO.")
                st.stop()
            f.historique_decisions.append(f"{dec} par {utilisateur()} ({role()}) le {maintenant()}"
                                          + (" (réouverture après NO GO)" if f.decision == "NO GO" else ""))
            f.decision, f.decideur, f.date_decision = dec, utilisateur(), datetime.now()
            a.valider("decision", "decision", utilisateur(), commentaire=dec, role=role())
            modifie = True
        if f.decision != "GO":
            for l in c.lots:  # aucune sélection de lot sans GO
                l.retenu = False if f.decision == "NO GO" else None
        if go:
            f.responsable_visite = resp or None
            f.date_visite = date_v.strftime("%d/%m/%Y") if date_v else None
        f.commentaires = commentaires or None
        if modifie:
            signaler_modification(c, "fiche")
        synchroniser_pieces(c)
        a.valider("fiche", "fiche", utilisateur(), role=role())
        a.sauver()
        st.toast("Fiche enregistrée.", icon=":material/check_circle:")
        st.rerun()
    if go and not any(l.retenu for l in c.lots):
        st.info("GO enregistré : coche maintenant les lots retenus puis enregistre.", icon=":material/check_box:")
    if f.decision == "NO GO":
        st.warning("NO GO : aucune préparation définitive de réponse sans réouverture de la décision par la direction.", icon=":material/block:")
    if f.alertes:
        with st.expander(f"Points de vigilance repérés dans le DCE ({len(f.alertes)})", icon=":material/priority_high:"):
            for al in f.alertes:
                libelle, _, extrait = al.partition(" : « ")
                st.markdown(f"**{libelle}**")
                st.markdown(f'<div class="sie-citation">« {extrait}</div>', unsafe_allow_html=True)
    corriges = {k: ch for k, ch in f.champs().items() if ch.methode == "llm_correction" and ch.valeur_avant}
    if corriges:
        with st.expander(f"Corrections proposées par l'IA, à valider ({len(corriges)})", icon=":material/rule:", expanded=True):
            for k, ch in corriges.items():
                st.markdown(f"**{k}** : « {ch.valeur_avant} » → « {ch.valeur} »  \n"
                            f"<span style='color:#666'>{ch.source.fichier} p.{ch.source.page} : « {ch.source.extrait} »</span>",
                            unsafe_allow_html=True)
                b1, b2 = st.columns(2)
                if b1.button("Garder la correction", key=f"ok_{k}"):
                    setattr(f, k, ch.model_copy(update={"statut": Statut.VALIDE, "valeur_avant": None}))
                    a.sauver(); st.rerun()
                if b2.button("Revenir à l'ancienne valeur", key=f"non_{k}"):
                    setattr(f, k, ch.model_copy(update={"valeur": ch.valeur_avant, "valeur_avant": None, "statut": Statut.CORRIGE,
                                                        "methode": "saisie"}))
                    a.sauver(); st.rerun()
    if moteur_choisi() != "aucun":
        with st.expander("Faire relire la fiche par l'IA (compléter et corriger)", icon=":material/auto_awesome:"):
            st.caption("L'IA relit tous les champs non validés dans le RC, l'AE et le CCAP. Chaque ajout ou correction cite "
                       "la phrase qui le justifie ; une correction garde l'ancienne valeur et attend ta validation. Aucun calcul.")
            moteur = moteur_pour(a)
            if moteur and st.button("Lancer la relecture", key="ia_fiche"):
                from sie_tender.f1_fiche import _relire_llm
                with st.spinner("L'IA relit la fiche…"):
                    _relire_llm(c.fiche, pieces_texte(a), moteur, c.journal)
                a.sauver()
                st.rerun()
    with st.expander("Historique des décisions et validations", icon=":material/history:"):
        for h in f.historique_decisions:
            st.markdown(f"- {h}")
        historique(a)
    e1, e2, e3 = st.columns(3)
    with e1:
        bouton_export(a, "Générer PDF / imprimer", "Fiche_renseignement.pdf", "fiche")
    with e2:
        bouton_export(a, "Fiche et analyse (Excel)", "analyse_dce.xlsx")
    with e3:
        modele = a.dossier("exports") / "modele_fiche.xlsx"
        if modele.exists():
            bouton_export(a, "Fiche au modèle SIE (Excel)", "Fiche_renseignement.xlsx")
        with st.popover("Modèle de fiche SIE", icon=":material/upload_file:"):
            st.caption("Fichier Excel de SIE avec des balises : {{reference}}, {{objet}}, {{lieu}}, {{maitre_ouvrage}}, "
                       "{{date_limite_remise}}, {{visite}}, {{intervenants}}, {{decision}}, {{responsable_visite}}, "
                       "{{date_visite}}... Une ligne avec {{lot_numero}}, {{lot_intitule}}, {{lot_retenu}} est répétée pour chaque lot.")
            m = st.file_uploader("Modèle (.xlsx)", type=["xlsx"], key=f"modele_fiche_{a.id}")
            if m and st.button("Enregistrer le modèle", type="primary"):
                a.deposer("exports", "modele_fiche.xlsx", m.getvalue())
                st.rerun()
    suivant("analyse", "analyse du DCE")


# --- Analyse du DCE ----------------------------------------------------------------------------
def page_analyse() -> None:
    a = affaire_courante()
    c = a.c
    entete("Analyse du DCE", "Exigences, critères, interfaces et registre de vigilance", a=a)
    if not c.registre and c.pieces:
        initialiser_registre(c)
        a.sauver()
    if c.criteres:
        cols = st.columns(min(len(c.criteres), 5))
        for i, (col, cr) in enumerate(zip(cols, c.criteres), start=1):
            carte(col, f"Critère {i}", f"{cr.ponderation:g} %" if cr.ponderation is not None else "?", cr.intitule[:60])
        st.write("")
    retenus = [l.numero for l in c.lots if l.retenu]
    if c.fiche.decision != "GO" or not retenus:
        st.info("L'analyse transverse couvre toutes les pièces, y compris les lots non retenus. L'approfondissement par lot porte "
                "sur les lots cochés après GO (fiche)." if c.fiche.decision != "NO GO" else "Décision NO GO : affaire non poursuivie.",
                icon=":material/info:")
    s = synthese_registre(c)
    interfaces = matrice_interfaces(c)
    onglets = st.tabs([f"Registre de vigilance ({s['ouverts']} ouverts)", f"Lots retenus ({len(retenus)})", f"Interfaces ({len(interfaces)})",
                       f"Exigences ({len(c.exigences)})", f"Critères ({len(c.criteres)})", f"Pièces exigées ({len(c.pieces_a_fournir)})",
                       "Assistant IA", "Journal"])
    with onglets[0]:
        registre(a)
    with onglets[1]:
        if not retenus:
            st.caption("Aucun lot retenu pour l'instant.")
        heures = heures_insertion(c.exigences)
        for s_lot in syntheses_retenues(c, pieces_texte(a)):
            l = s_lot.lot
            with st.expander(f"Lot {l.numero} : {l.intitule}" + (f" · {', '.join(l.activites)}" if l.activites else ""),
                             expanded=len(retenus) == 1, icon=":material/inventory:"):
                k = st.columns(4)
                carte(k[0], "Prestations (CCTP)", str(len(s_lot.prestations)))
                carte(k[1], "Risques", str(len(s_lot.risques)), "éliminatoire, pénalités, site occupé")
                carte(k[2], "Interfaces", str(len(s_lot.interfaces)))
                carte(k[3], "Heures d'insertion", ", ".join(e.texte for e in s_lot.heures_insertion) or "non demandé",
                      ref(s_lot.heures_insertion[0].source) if s_lot.heures_insertion else "")
                st.write("")
                for titre_s, elements in (("Prestations", s_lot.prestations), ("Exigences techniques", s_lot.exigences_techniques),
                                          ("Risques", s_lot.risques), ("RSE", s_lot.rse), ("Planning", s_lot.planning)):
                    if elements:
                        st.markdown(f"**{titre_s}**")
                        st.dataframe(pd.DataFrame([{"Élément": e.texte, "Source": ref(e.source)} for e in elements]),
                                     hide_index=True, width="stretch", column_config={"Élément": st.column_config.TextColumn(width="large")})
                if s_lot.questions:
                    st.markdown("**Questions et points à vérifier**")
                    for q in s_lot.questions:
                        st.markdown(f"- {q}")
        if retenus:
            bouton_export(a, "Télécharger la synthèse (Word)", "Synthese_lots_retenus.docx")
        elif heures:
            st.caption("Heures d'insertion demandées : " + ", ".join(f"{h} h ({ref(e.source)})" for h, e in heures))
    with onglets[2]:
        st.caption("Interfaces relevées dans les CCTP de tous les lots et dans les pièces communes, avec les contraintes "
                   "communes (site occupé, phasage). Chaque ligne cite le DCE.")
        axes, grille = tableau_croise(interfaces, c.lots)
        if interfaces and c.lots:
            st.markdown("**Matrice (nombre d'interfaces, lot d'origine en ligne, lot concerné en colonne)**")
            mat = pd.DataFrame([[grille[o][l.numero] for l in c.lots] for o in axes],
                               index=[o if o == "commun" else f"Lot {o}" + (" ★" if o in retenus else "") for o in axes],
                               columns=[f"Lot {l.numero}" + (" ★" if l.retenu else "") for l in c.lots])
            st.dataframe(mat.replace(0, ""), width="stretch")
            st.caption("★ lot retenu par SIE")
        seulement = st.toggle("Seulement les impacts sur les lots retenus", value=bool(retenus), disabled=not retenus)
        vues = [i for i in interfaces if i.impacte_retenu or not seulement]
        st.dataframe(pd.DataFrame([{"Origine": i.origine, "Lots concernés": ", ".join(i.concernes), "Nature": ", ".join(i.themes),
                                    "Texte du DCE": i.texte, "Source": ref(i.source)} for i in vues]),
                     hide_index=True, width="stretch", column_config={"Texte du DCE": st.column_config.TextColumn(width="large")})
        if not interfaces:
            st.warning("Aucune interface trouvée : vérifier les CCTP des autres lots.", icon=":material/warning:")
    with onglets[3]:
        tous_lots = sorted({e.lot or "commun" for e in c.exigences}, key=lambda x: (x == "commun", x.zfill(3)))
        cats = sorted({e.categorie.value for e in c.exigences})
        f1, f2, f3 = st.columns([2, 3, 3])
        lots = f1.multiselect("Lots", tous_lots, default=[x for x in tous_lots if not retenus or x in retenus + ["commun"]])
        choix_cats = f2.multiselect("Catégories", cats, default=cats, format_func=lambda x: CATEGORIES.get(x, x))
        texte = f3.text_input("Contient", placeholder="mot-clé…")
        ex = [e for e in c.exigences if (e.lot or "commun") in lots and e.categorie.value in choix_cats
              and (not texte or texte.lower() in e.texte.lower())]
        st.dataframe(pd.DataFrame([{"Lot": e.lot or "commun", "Catégorie": CATEGORIES.get(e.categorie.value, e.categorie.value),
                                    "Précision": e.sous_categorie or "", "Exigence": e.texte, "Source": ref(e.source)} for e in ex]),
                     width="stretch", hide_index=True, height=min(38 + 35 * max(len(ex), 1), 560),
                     column_config={"Exigence": st.column_config.TextColumn(width="large")})
        st.caption("Chaque exigence extraite est à valider dans le registre de vigilance ou dans la pièce source.")
    with onglets[4]:
        if not c.criteres:
            st.warning("Critères non trouvés dans le RC : à saisir.")
        total = sum(cr.ponderation or 0 for cr in c.criteres)
        if c.criteres and abs(total - 100) > 0.01:
            st.warning(f"La somme des pondérations fait {total:g} % : à vérifier dans le RC.")
        for cr in c.criteres:
            st.markdown(f"**{cr.intitule}** · {(cr.ponderation or 0):g} %")
            st.progress(min((cr.ponderation or 0) / 100, 1.0))
            for sc in cr.sous_criteres:
                st.markdown(f"&nbsp;&nbsp;&nbsp;– {sc.intitule} : {(sc.ponderation or 0):g} %")
            st.caption(ref(cr.source))
    with onglets[5]:
        st.dataframe(pd.DataFrame([{"Étape": p.etape, "Pièce": p.intitule, "Document d'archive proposé": p.document_propose or "",
                                    "Validité": p.validite, "Statut": STATUT_AFFICHE[p.statut], "Source": ref(p.source)}
                                   for p in c.pieces_a_fournir]), hide_index=True, width="stretch",
                     column_config={"Validité": st.column_config.DateColumn(format="DD/MM/YYYY")})
        st.page_link(PAGES["documents"], label="Suivre ces pièces dans Documents", icon=":material/arrow_forward:")
    with onglets[6]:
        assistant(a)
    with onglets[7]:
        st.caption("Hypothèses, erreurs et limites relevées pendant l'analyse.")
        for j in c.journal:
            st.markdown("- " + j)
    suivant("metres", "décomptes et métrés")


def registre(a: Affaire) -> None:
    c = a.c
    s = synthese_registre(c)
    k = st.columns(4)
    carte(k[0], "À traiter", str(s["ouverts"]))
    carte(k[1], "Bloquants", str(s["bloquants"]))
    carte(k[2], "À intégrer au mémoire", str(s["memoire"]), f"{s['memoire_ouverts']} encore ouvert(s)")
    carte(k[3], "Traités / sans objet", str(s["traites"]))
    st.caption("Liste de contrôle et points repérés dans le DCE. Chaque ligne est à vérifier dans le document source, avec la page, "
               "l'action et son statut. Les lignes « liste de contrôle » ne prouvent pas que l'exigence figure dans ce DCE. "
               "Une alerte « Bloquant » reste visible jusqu'au contrôle final.")
    r = refs()
    personnel = sorted(set(noms_personnel(r) + [x.responsable for x in c.registre if x.responsable]))
    f1, f2 = st.columns([2, 3])
    seulement = f1.toggle("Masquer les points traités", value=False)
    vues = [x for x in c.registre if not seulement or x.statut in OUVERTS]
    df = pd.DataFrame([{"id": x.id, "Point à contrôler": x.intitule, "Catégorie": x.categorie, "Lot": x.lot or "",
                        "Source": x.source or "", "Page": x.page or "", "Action et observations": x.action or "",
                        "Responsable": x.responsable or "", "Échéance": x.echeance or "", "Statut": x.statut,
                        "Destination": x.destination, "Mémoire": x.memoire, "Priorité": x.priorite, "Extrait": x.extrait or ""}
                       for x in vues],
                      columns=["id", "Point à contrôler", "Catégorie", "Lot", "Source", "Page", "Action et observations", "Responsable",
                               "Échéance", "Statut", "Destination", "Mémoire", "Priorité", "Extrait"])
    df2 = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic", key=f"reg_{a.id}_{seulement}",
                         height=min(38 + 35 * (len(df) + 1), 700),
                         column_config={"id": None, "Point à contrôler": st.column_config.TextColumn(width="large", required=True),
                                        "Statut": st.column_config.SelectboxColumn(options=STATUTS_ALERTE, required=True),
                                        "Destination": st.column_config.SelectboxColumn(options=DESTINATIONS_ALERTE),
                                        "Responsable": st.column_config.SelectboxColumn(options=personnel) if personnel else st.column_config.TextColumn(),
                                        "Priorité": st.column_config.SelectboxColumn(options=["haute", "normale"]),
                                        "Mémoire": st.column_config.CheckboxColumn(help="Point à intégrer au mémoire technique"),
                                        "Extrait": st.column_config.TextColumn(disabled=True)})
    b1, b2, b3 = st.columns(3)
    if b1.button("Enregistrer le registre", type="primary", icon=":material/save:"):
        par_id = {x.id: x for x in c.registre}
        caches = [x for x in c.registre if x not in vues]
        nouveaux = []
        memo_avant = {(x.id, x.memoire, x.statut) for x in c.registre if x.memoire}
        for _, row in df2.iterrows():
            if not str(row["Point à contrôler"] or "").strip():
                continue
            x = par_id.get(row["id"]) if isinstance(row["id"], str) else None
            x = x or Alerte(intitule="", origine="saisie")
            x.intitule, x.categorie = str(row["Point à contrôler"]).strip(), row["Catégorie"] or "Administratif"
            for champ, col in (("lot", "Lot"), ("source", "Source"), ("page", "Page"), ("action", "Action et observations"),
                               ("responsable", "Responsable"), ("echeance", "Échéance")):
                v = row[col]
                setattr(x, champ, None if v is None or (isinstance(v, float) and pd.isna(v)) or not str(v).strip() else str(v).strip())
            x.statut, x.destination = row["Statut"] or "À vérifier", row["Destination"] or "Contrôle final"
            x.memoire, x.priorite = bool(row["Mémoire"]), row["Priorité"] or "normale"
            nouveaux.append(x)
        c.registre = nouveaux + caches
        if memo_avant != {(x.id, x.memoire, x.statut) for x in c.registre if x.memoire}:
            signaler_modification(c, "registre")
        a.sauver()
        st.toast("Registre enregistré.", icon=":material/check_circle:")
        st.rerun()
    with b2:
        bouton_export(a, "Synthèse PDF / imprimer", "Registre_vigilance.pdf")
    if b3.button("Ajouter les points manquants du DCE", icon=":material/playlist_add:"):
        n = initialiser_registre(c)
        a.sauver()
        st.toast(f"{n} point(s) ajouté(s).")
        st.rerun()
    st.markdown(f"**Synthèse pour la réponse** : {s['ouverts']} point(s) encore ouvert(s), dont {s['bloquants']} bloquant(s) ; "
                f"{s['memoire']} point(s) cochés pour le mémoire technique, transmis à la rubrique Mémoire et au contrôle final.")


def assistant(a: Affaire) -> None:
    c = a.c
    moteur = moteur_pour(a)
    if moteur is not None:
        st.caption(f"Moteur : {NOMS_MOTEURS.get(moteur_choisi())} ({moteur.nom}). Chaque réponse cite les pages utilisées ; "
                   "les citations sont revérifiées mot pour mot dans les pièces.")
        c1, c2 = st.columns([1, 3])
        if c1.button("Synthèse pour la décision", type="primary", icon=":material/summarize:", width="stretch"):
            if appeler_ia(a, ia.synthese_dce, c, pieces_texte(a), moteur):
                st.rerun()
        with c2.form("question", clear_on_submit=True, border=False):
            q1, q2 = st.columns([5, 1])
            question = q1.text_input("Question", placeholder="Ex. : quelles pénalités en cas de retard ? Qui fournit les luminaires ?",
                                     label_visibility="collapsed")
            if q2.form_submit_button("Demander", width="stretch") and question.strip():
                if appeler_ia(a, ia.question_dce, pieces_texte(a), question.strip(), moteur):
                    st.rerun()
    reponses = [r for r in c.reponses_ia if r.type in ("synthese", "question")]
    if not reponses:
        return
    total = sum(r.cout_usd for r in c.reponses_ia)
    st.caption(f"{len(reponses)} réponse(s) · coût IA cumulé sur cette affaire : {total:.2f} $")
    for r in reversed(reponses):
        with st.container(border=True):
            st.markdown(f"**{r.question}**")
            afficher_reponse(a, r)


# --- Décomptes & métrés --------------------------------------------------------------------------
ROLES_COLONNES = {"code": "Référence", "designation": "Désignation", "unite": "Unité", "quantite": "Quantité",
                  "famille": "Famille / chapitre", "lot": "Lot"}


def page_metres() -> None:
    a = affaire_courante()
    c = a.c
    entete("Décomptes et métrés · import Excel", "F3 : quantités relevées par le bureau d'études ; aucun métré automatique des plans", a=a)
    lots = [l.numero for l in c.lots]
    retenus = [l.numero for l in c.lots if l.retenu]
    with st.expander("1. Charger un fichier de décompte et de métré", expanded=not c.quantites, icon=":material/upload_file:"):
        st.caption("Le bureau d'études réalise les décomptes hors outil. Importer ici son tableau (XLSX, XLS ou CSV), choisir la feuille "
                   "et les colonnes, puis vérifier. Plusieurs imports par lot sont possibles ; chaque fichier source est conservé.")
        f = st.file_uploader("Fichier de métré", type=["xlsx", "xlsm", "xls", "csv"], key=f"metre_{a.id}")
        if f:
            tmp = a.dossier("quantites") / f"_apercu_{f.name}"
            tmp.write_bytes(f.getvalue())
            try:
                noms = feuilles(tmp)
            except Exception as e:
                st.error(f"Fichier illisible : {e}")
                return
            c1, c2 = st.columns(2)
            feuille = c1.selectbox("Feuille", ["(toutes les feuilles)"] + noms if len(noms) > 1 else noms)
            lot_defaut = c2.selectbox("Lot concerné", lots or [""], index=(lots.index(retenus[0]) if retenus and retenus[0] in lots else 0),
                                      help="Utilisé si la feuille ou une colonne n'indique pas le lot.")
            feuille_choisie = None if feuille == "(toutes les feuilles)" else feuille
            entetes, detecte, ligne_entete = apercu(tmp, feuille_choisie or noms[0])
            st.markdown("**Correspondance des colonnes**")
            m = st.columns(len(ROLES_COLONNES))
            mapping = {}
            for col, (cle, lib) in zip(m, ROLES_COLONNES.items()):
                opts = ["(aucune)"] + entetes
                defaut = entetes[detecte[cle]] if cle in detecte and detecte[cle] < len(entetes) else "(aucune)"
                choix = col.selectbox(lib, opts, index=opts.index(defaut), key=f"map_{cle}")
                if choix != "(aucune)":
                    mapping[cle] = entetes.index(choix)
            manuel = mapping != {k: v for k, v in detecte.items() if k in ROLES_COLONNES}
            essai, _ = importer_quantites(tmp, lot_defaut or None, feuille_choisie, mapping if manuel else None,
                                          ligne_entete if manuel else None) if {"designation", "quantite"} <= mapping.keys() else ([], [])
            if not {"designation", "quantite"} <= mapping.keys():
                st.warning("Choisir au moins les colonnes Désignation et Quantité.")
            else:
                ctl = controler_metre(essai)
                st.caption(f"Aperçu : {ctl['total']} lignes · {ctl['doublons']} doublon(s) possible(s) · {ctl['unites']} unité(s) "
                           f"manquante(s) ou ambiguë(s) · {ctl['quantites']} quantité(s) manquante(s) ou incohérente(s)")
                st.dataframe(pd.DataFrame([{"Lot": q.lot, "Réf.": q.code, "Désignation": q.designation, "Unité": q.unite,
                                            "Quantité": q.quantite, "Ligne": q.source.ligne if q.source else None, "À vérifier": q.remarque or ""}
                                           for q in essai[:200]]), hide_index=True, width="stretch", height=260)
                if st.button("Importer ces lignes", type="primary", disabled=not essai):
                    nom = f.name
                    versions = [i for i in c.imports_metre if i.fichier.split("_v")[0] == Path(nom).stem or i.fichier == nom]
                    if (a.dossier("quantites") / nom).exists():
                        nom = f"{Path(nom).stem}_v{len(versions) + 1}{Path(nom).suffix}"
                    chemin = a.deposer("quantites", nom, f.getvalue())
                    lignes, journal = importer_quantites(chemin, lot_defaut or None, feuille_choisie, mapping if manuel else None,
                                                         ligne_entete if manuel else None)
                    c.quantites += lignes
                    c.journal += journal
                    c.imports_metre.append(ImportMetre(fichier=nom, feuille=feuille_choisie, lot=lot_defaut or None,
                                                       colonnes={ROLES_COLONNES[k]: entetes[v] for k, v in mapping.items()},
                                                       version=len(versions) + 1, nb_lignes=len(lignes), auteur=utilisateur()))
                    signaler_modification(c, "quantites")
                    tmp.unlink(missing_ok=True)
                    a.sauver()
                    st.toast(journal[-1], icon=":material/check_circle:")
                    st.rerun()
    if not c.quantites:
        st.info("Aucun tableau de métrés importé pour cette consultation.")
        return
    filtre_lot = st.segmented_control("Lot", ["Tous"] + sorted({q.lot or "?" for q in c.quantites}), default="Tous")
    vues = [q for q in c.quantites if filtre_lot in (None, "Tous") or (q.lot or "?") == filtre_lot]
    ctl = controler_metre(vues)
    cols = st.columns(4)
    carte(cols[0], "Lignes", str(len(vues)))
    carte(cols[1], "À vérifier", str(sum(q.statut == Statut.A_VERIFIER for q in vues)))
    carte(cols[2], "Doublons possibles", str(ctl["doublons"]))
    carte(cols[3], "Validées", str(sum(q.statut == Statut.VALIDE for q in vues)))
    st.write("")
    df = pd.DataFrame([{"id": q.id, "Lot": q.lot, "Réf.": q.code, "Famille": q.famille, "Désignation": q.designation,
                        "Unité": q.unite, "Quantité": q.quantite,
                        "Source": f"{q.source.fichier} · {q.source.feuille or ''} l.{q.source.ligne}" if q.source else "saisie",
                        "Remarque": q.remarque or ""} for q in vues])
    df2 = st.data_editor(df, column_config={"id": None, "Quantité": st.column_config.NumberColumn(min_value=0, format="%.2f"),
                                            "Désignation": st.column_config.TextColumn(width="large")},
                         disabled=["Remarque", "Source"], hide_index=True, width="stretch", num_rows="dynamic", key=f"q_{a.id}_{filtre_lot}")
    if st.button("Enregistrer et valider les quantités", type="primary", icon=":material/save:"):
        par_id = {q.id: q for q in c.quantites}
        caches = [q for q in c.quantites if q not in vues]
        avant = [(q.id, q.quantite, q.unite) for q in c.quantites]
        nouvelles = []
        for _, r in df2.iterrows():
            q = par_id.get(r["id"]) if isinstance(r["id"], str) else None
            if q is None:
                q = LigneQuantite(designation=str(r["Désignation"] or ""), remarque="ligne saisie à la main")
            q.lot, q.code, q.famille, q.designation, q.unite = r["Lot"], r["Réf."], r["Famille"], r["Désignation"], r["Unité"]
            q.quantite = None if pd.isna(r["Quantité"]) else float(r["Quantité"])
            ok = q.quantite is not None and q.quantite > 0 and q.unite and "doublon" not in (q.remarque or "")
            q.statut = Statut.VALIDE if ok else Statut.A_VERIFIER
            if ok:
                q.remarque = None
            elif not q.remarque:
                q.remarque = "quantité ou unité manquante"
            nouvelles.append(q)
        c.quantites = nouvelles + caches
        if avant != [(q.id, q.quantite, q.unite) for q in c.quantites]:
            signaler_modification(c, "quantites")
        a.valider("quantites", "quantites", utilisateur(), role=role())
        a.sauver()
        st.toast("Quantités enregistrées.", icon=":material/check_circle:")
        st.rerun()
    st.caption("Une ligne signalée « doublon possible » reste à vérifier : corrige-la ou supprime-la, puis enregistre.")
    if c.imports_metre:
        with st.expander(f"Imports enregistrés ({len(c.imports_metre)})", icon=":material/history:"):
            st.dataframe(pd.DataFrame([{"Fichier": i.fichier, "Version": i.version, "Feuille": i.feuille or "toutes", "Lot": i.lot,
                                        "Lignes": i.nb_lignes, "Colonnes": ", ".join(f"{k} = {v}" for k, v in i.colonnes.items()),
                                        "Par": i.auteur, "Date": i.date} for i in c.imports_metre]), hide_index=True, width="stretch",
                         column_config={"Date": st.column_config.DatetimeColumn(format="DD/MM/YYYY HH:mm")})
    suivant("demandes", "demandes de prix")


# --- Demandes de prix fournisseurs ------------------------------------------------------------------
def page_demandes() -> None:
    a = affaire_courante()
    c = a.c
    r = refs()
    entete("Demandes de prix fournisseurs", "Chaque fournisseur reçoit seulement ses articles et les pages utiles du CCTP", a=a)
    if not c.quantites:
        st.info("Importe d'abord les métrés (rubrique Décomptes & métrés).")
        return
    if any(q.statut != Statut.VALIDE for q in c.quantites):
        st.warning("Des quantités ne sont pas encore validées : à vérifier avant toute demande réelle.", icon=":material/warning:")
    with st.container(border=True):
        st.markdown("**Préparer une demande de prix ciblée** · choix manuel · aucun envoi automatique")
        st.markdown("**1. Fournisseur destinataire**")
        familles = sorted({q.famille or "Divers" for q in c.quantites})
        c1, c2, c3 = st.columns([2, 2, 2])
        famille = c1.selectbox("Famille ou spécialité", ["(toutes)"] + familles)
        fournisseurs = r.fournisseurs_pour(None if famille == "(toutes)" else famille)
        if not fournisseurs:
            c2.warning("Aucun fournisseur actif.")
            st.page_link(PAGES["parametres"], label="Ajouter des fournisseurs dans Paramètres", icon=":material/settings:")
            fournisseur = None
        else:
            fournisseur = c2.selectbox("Fournisseur", fournisseurs, format_func=lambda f: f.raison_sociale
                                       + (f" ({', '.join(f.specialites)})" if f.specialites else ""), index=None,
                                       placeholder="— Choisir un fournisseur —",
                                       help="Les fournisseurs dont la spécialité correspond sont en tête ; aucun n'est choisi d'office.")
        contact = None
        if fournisseur and fournisseur.contacts:
            contact = c3.selectbox("Contact", fournisseur.contacts, format_func=lambda x: f"{x.libelle} · {x.email or 'sans e-mail'}")
        st.markdown("**2. Articles et quantités à consulter**")
        lignes = [q for q in c.quantites if famille == "(toutes)" or (q.famille or "Divers") == famille]
        df = pd.DataFrame([{"id": q.id, "Inclure": False, "Lot": q.lot, "Réf.": q.code, "Fourniture / prestation": q.designation,
                            "Quantité": q.quantite, "Unité": q.unite, "Validée": q.statut == Statut.VALIDE} for q in lignes])
        choix = st.data_editor(df, hide_index=True, width="stretch", key=f"art_{a.id}_{famille}",
                               disabled=["Lot", "Réf.", "Fourniture / prestation", "Quantité", "Unité", "Validée"],
                               column_config={"id": None, "Fourniture / prestation": st.column_config.TextColumn(width="large")})
        ids = [x["id"] for _, x in choix.iterrows() if x["Inclure"]]
        st.markdown("**3. CCTP et extraits personnalisés**")
        st.caption("Pages du PDF (et non la pagination imprimée), plages non consécutives possibles : ex. 12-15, 38-42. "
                   "Les pages non sélectionnées sont exclues de l'extrait.")
        cctp_dce = [f for f in sorted(a.dossier("dce").glob("*.pdf")) if "cctp" in f.name.lower()]
        e1, e2 = st.columns(2)
        source = e1.selectbox("CCTP", ["(aucun extrait)"] + [f.name for f in cctp_dce] + ["Autre PDF…"])
        pdf = None
        if source == "Autre PDF…":
            autre = e1.file_uploader("CCTP (PDF)", type=["pdf"], key=f"cctp_{a.id}")
            if autre:
                pdf = a.deposer("exports", f"cctp_{autre.name}", autre.getvalue())
        elif source != "(aucun extrait)":
            pdf = a.dossier("dce") / source
        nb_pages = None
        if pdf:
            import pymupdf
            with pymupdf.open(pdf) as doc:
                nb_pages = doc.page_count
        plages_txt = e2.text_input("Plages de pages", placeholder="12-15, 38-42", disabled=pdf is None,
                                   help=f"Le PDF compte {nb_pages} pages." if nb_pages else None)
        plages, erreurs = lire_plages(plages_txt, nb_pages) if pdf and plages_txt else ([], [])
        for e in erreurs:
            e2.error(e)
        st.markdown("**4. Finaliser le brouillon**")
        f1, f2 = st.columns([1, 2])
        limite = lire_date(c.fiche.date_limite_remise.valeur)
        echeance = f1.date_input("Date limite souhaitée de retour", value=None, format="DD/MM/YYYY",
                                 help=f"Remise des offres : {limite:%d/%m/%Y}" if limite else None)
        instructions = f2.text_area("Instructions fournisseur", height=80, placeholder="Délais, variantes acceptées, marques équivalentes…")
        pret = fournisseur is not None and ids and not erreurs and (pdf is None or plages)
        if st.button("Générer la demande de prix pour ce fournisseur", type="primary", disabled=not pret, icon=":material/task_alt:"):
            d = demande_fournisseur(c, fournisseur, ids, contact, echeance.strftime("%d/%m/%Y") if echeance else None, instructions,
                                    plages, pdf.name if pdf else None, utilisateur())
            if pdf and plages:
                dossier = a.dossier("exports") / "demandes"
                dossier.mkdir(exist_ok=True)
                sortie = extraire_pages(pdf, plages, dossier / f"Extrait_CCTP_{fournisseur.raison_sociale[:30].replace(' ', '_')}_{d.id}.pdf")
                d.extrait_fichier = str(sortie.relative_to(a.racine))
            c.demandes_cotation.append(d)
            fournisseur.historique.append(f"{c.fiche.reference.valeur or a.id} : demande {d.id} le {maintenant()}")
            sauver_refs(r)
            a.sauver()
            st.toast("Brouillon de demande créé.", icon=":material/check_circle:")
            st.rerun()
        if not pret:
            st.caption("À compléter : fournisseur, au moins un article" + (", plages de pages valides" if pdf else "") + ".")
        st.caption("L'outil prépare les documents. Le choix final des destinataires, la vérification des pièces et l'envoi restent faits par SIE.")
    st.subheader(f"Demandes préparées ({len(c.demandes_cotation)})")
    if not c.demandes_cotation:
        st.caption("Aucune demande préparée.")
    for d in c.demandes_cotation:
        nom = d.fournisseurs[0] if d.fournisseurs else "(fournisseur à choisir)"
        with st.expander(f"{nom} · {d.famille} · {len(d.lignes)} ligne(s) · v{d.version} · {d.statut}"):
            s1, s2 = st.columns([1, 2])
            statut = s1.selectbox("Suivi", STATUTS_DEMANDE, index=STATUTS_DEMANDE.index(d.statut) if d.statut in STATUTS_DEMANDE else 0,
                                  key=f"st_{d.id}")
            if statut != d.statut:
                d.historique.append(f"{maintenant()} {d.statut} → {statut} ({utilisateur()})")
                d.statut = statut
                a.sauver()
                st.rerun()
            s2.caption(" · ".join(d.historique[-4:]))
            st.dataframe(pd.DataFrame([{"Désignation": l.designation, "Unité": l.unite, "Quantité": l.quantite,
                                        "Caractéristiques (CCTP)": "\n".join(l.caracteristiques)} for l in d.lignes]),
                         hide_index=True, width="stretch")
            st.text_area("Brouillon d'e-mail", courriel_fournisseur(c, d) if d.fournisseur_id else texte_courriel(c, d), height=240, key=f"mail_{d.id}")
            if d.email and not email_valide(d.email):
                st.warning(f"Adresse à vérifier : {d.email}")
            b1, b2, b3 = st.columns(3)
            with tempfile.TemporaryDirectory() as t:
                b1.download_button("Bordereau (Excel)", exporter_demande(c, d, Path(t) / "d.xlsx").read_bytes(),
                                   file_name=f"Demande_prix_{nom[:30]}_{d.id}.xlsx", key=f"xl_{d.id}", icon=":material/download:")
            if d.extrait_fichier and (a.racine / d.extrait_fichier).exists():
                b2.download_button("Extrait CCTP (PDF)", (a.racine / d.extrait_fichier).read_bytes(), file_name=Path(d.extrait_fichier).name,
                                   key=f"pdf_{d.id}", icon=":material/download:")
            if b3.button("Nouvelle version", key=f"v_{d.id}", icon=":material/content_copy:", help="Garde cette version et en crée une copie modifiable."):
                n = d.model_copy(deep=True, update={"id": f"{d.id.split('-v')[0]}-v{d.version + 1}", "version": d.version + 1,
                                                    "statut": "brouillon", "historique": [f"{maintenant()} copie de la v{d.version}"]})
                c.demandes_cotation.append(n)
                a.sauver()
                st.rerun()
    with st.expander("Brouillons groupés par famille (sans fournisseur)", icon=":material/mail:"):
        st.caption("Ancienne méthode : une demande par lot et famille, à compléter par SIE.")
        delai = st.number_input("Retour des devis, jours avant la remise", 3, 30, 10)
        if st.button("Générer les brouillons par famille"):
            c.demandes_cotation += generer_demandes(c, delai)
            a.sauver()
            st.rerun()
        if c.demandes_cotation:
            bouton_export(a, "Toutes les demandes (Excel)", "demandes_cotation.xlsx")
    suivant("dpgf", "chiffrage et DPGF")


# --- Chiffrage / DPGF ----------------------------------------------------------------------------
def page_dpgf() -> None:
    a = affaire_courante()
    r = refs()
    entete("Chiffrage et DPGF", "Réception et comparaison des devis, choix manuel par poste, DPGF au modèle SIE", a=a)
    onglets = st.tabs(["Devis reçus et comparaison", "DPGF"])
    with onglets[0]:
        devis_et_comparatif(a, r)
    with onglets[1]:
        dpgf(a)


def devis_et_comparatif(a: Affaire, r: Referentiels) -> None:
    c = a.c
    with st.expander("Enregistrer un devis reçu", expanded=not c.devis, icon=":material/upload_file:"):
        st.caption("Après réception par SIE, déposer le devis (Excel, CSV ou PDF texte). Les lignes sont lues puis rapprochées des postes ; "
                   "chaque rapprochement reste à valider.")
        c1, c2, c3 = st.columns([2, 2, 2])
        noms = [f.raison_sociale for f in r.fournisseurs if f.actif]
        fournisseur = c1.selectbox("Fournisseur", noms + ["Autre…"], index=None, placeholder="— Choisir —")
        if fournisseur == "Autre…" or not noms:
            fournisseur = c1.text_input("Nom du fournisseur")
        demandes = {d.id: f"{(d.fournisseurs or ['?'])[0]} · {d.famille} · v{d.version}" for d in c.demandes_cotation}
        demande = c2.selectbox("Demande de prix", [None] + list(demandes), format_func=lambda x: demandes.get(x, "(aucune)"))
        f = c3.file_uploader("Devis reçu", type=["xlsx", "csv", "pdf"])
        if st.button("Enregistrer le devis", disabled=not f, type="primary"):
            chemin = a.deposer("devis", f.name, f.getvalue())
            lot = next((l.numero for l in c.lots if l.retenu), None) if sum(bool(l.retenu) for l in c.lots) == 1 else None
            d = importer_devis(chemin, fournisseur or None, lot)
            d.demande_id = demande
            associer(c, d)
            c.devis.append(d)
            dem = next((x for x in c.demandes_cotation if x.id == demande), None)
            if dem and dem.statut != "réponse reçue":
                dem.historique.append(f"{maintenant()} {dem.statut} → réponse reçue")
                dem.statut = "réponse reçue"
            a.sauver()
            st.toast(f"{len(d.lignes)} lignes lues, {sum(1 for l in d.lignes if l.ligne_quantite_id)} rapprochées.", icon=":material/check_circle:")
            st.rerun()
    postes = {q.id: f"{q.code or ''} {q.designation}".strip() for q in c.quantites}
    options = ["(aucun)"] + list(postes.values())
    inverse = {v: k for k, v in postes.items()}
    for d in c.devis:
        n_assoc = sum(1 for l in d.lignes if l.ligne_quantite_id)
        with st.expander(f"{d.fournisseur} · {d.fichier} · {n_assoc}/{len(d.lignes)} lignes rapprochées"):
            df = pd.DataFrame([{"Désignation devis": l.designation, "Unité": l.unite, "Qté": l.quantite, "PU HT": l.prix_unitaire,
                                "Poste associé": postes.get(l.ligne_quantite_id, "(aucun)"), "Confiance": l.score_association,
                                "Validé": l.association_validee} for l in d.lignes])
            df2 = st.data_editor(df, column_config={"Poste associé": st.column_config.SelectboxColumn(options=options, width="large"),
                                                    "Confiance": st.column_config.ProgressColumn(min_value=0, max_value=1),
                                                    "PU HT": st.column_config.NumberColumn(format="%.2f €")},
                                 disabled=["Désignation devis", "Confiance"], hide_index=True, width="stretch", key=f"dv_{d.id}")
            if st.button("Enregistrer les rapprochements", key=f"save_{d.id}"):
                for l, (_, x) in zip(d.lignes, df2.iterrows()):
                    l.ligne_quantite_id = inverse.get(x["Poste associé"])
                    l.prix_unitaire = None if pd.isna(x["PU HT"]) else float(x["PU HT"])
                    l.association_validee = bool(x["Validé"])
                a.sauver()
                st.toast("Rapprochements enregistrés.", icon=":material/check_circle:")
    st.subheader("Comparatif et sélection des offres")
    st.caption("La meilleure offre n'est pas forcément la moins chère : SIE vérifie aussi conformité, délais, exclusions et conditions. "
               "L'outil ne choisit aucun fournisseur.")
    offres = offres_par_poste(c)
    choix = {s.ligne_quantite_id: s for s in c.selections}
    libelles = {d.id: f"{d.fournisseur} ({d.id[-4:]})" for d in c.devis}
    inv_lib = {v: k for k, v in libelles.items()}
    lignes = []
    for q in c.quantites:
        lst = offres.get(q.id, [])
        s = choix.get(q.id)
        lignes.append({"id": q.id, "Poste": postes[q.id], "Quantité": f"{q.quantite:g} {q.unite or ''}" if q.quantite is not None else "?",
                       "Offres reçues (PU HT)": " · ".join(f"{d.fournisseur} : {l.prix_unitaire:,.2f} €".replace(",", " ")
                                                           + (f" /{l.unite}" if l.unite and l.unite != q.unite else "")
                                                           for d, l in sorted(lst, key=lambda x: x[1].prix_unitaire)) or "Aucun prix reçu",
                       "Fournisseur retenu": libelles.get(s.devis_id) if s else None, "Motif du choix": (s.motif or "") if s else ""})
    if not lignes:
        st.caption("Aucun poste : importe les métrés.")
        return
    df = st.data_editor(pd.DataFrame(lignes), hide_index=True, width="stretch", key=f"comp_{a.id}",
                        disabled=["Poste", "Quantité", "Offres reçues (PU HT)"],
                        column_config={"id": None, "Offres reçues (PU HT)": st.column_config.TextColumn(width="large"),
                                       "Fournisseur retenu": st.column_config.SelectboxColumn(options=list(libelles.values()))})
    confirme = st.checkbox("Je confirme que les prix et devis sélectionnés ont été vérifiés par SIE.")
    if st.button("Appliquer les prix retenus à la DPGF", type="primary", disabled=not confirme, icon=":material/table:"):
        erreurs = []
        avant = {(s.ligne_quantite_id, s.devis_id) for s in c.selections}
        for _, x in df.iterrows():
            did = inv_lib.get(x["Fournisseur retenu"]) if x["Fournisseur retenu"] else None
            if did and not any(d.id == did for d, _ in offres.get(x["id"], [])):
                erreurs.append(f"{x['Poste']} : {x['Fournisseur retenu']} n'a pas chiffré ce poste.")
                continue
            ancien = choix.get(x["id"])
            if did and ancien and ancien.devis_id == did and (ancien.motif or "") == (x["Motif du choix"] or ""):
                continue
            retenir(c, x["id"], did, x["Motif du choix"] or None, utilisateur())
        for e in erreurs:
            st.error(e)
        if avant != {(s.ligne_quantite_id, s.devis_id) for s in c.selections}:
            signaler_modification(c, "selections")
        c.dpgf = construire_dpgf(c)
        a.sauver()
        if not erreurs:
            st.toast("Choix enregistrés, DPGF recalculée.", icon=":material/check_circle:")
            st.rerun()


def dpgf(a: Affaire) -> None:
    c = a.c
    st.caption("Coefficients saisis par SIE : sans eux, le prix de vente reste vide. Main-d'œuvre, frais, aléas et marge sont validés par SIE.")
    k = c.coefficients
    with st.form("coefficients", border=False):
        cols = st.columns(4)
        vals = [cols[i].number_input(lbl, value=v, min_value=0.0, step=0.5, format="%.2f", key=f"k{i}")
                for i, (lbl, v) in enumerate([("Taux horaire €/h", k.taux_horaire), ("Frais généraux %", k.frais_generaux),
                                              ("Aléas %", k.aleas), ("Marge %", k.marge)])]
        modele = st.file_uploader("Modèle de DPGF SIE ou de l'acheteur (facultatif)", type=["xlsx"])
        if st.form_submit_button("Construire la DPGF", type="primary", disabled=not c.quantites, icon=":material/table:"):
            k.taux_horaire, k.frais_generaux, k.aleas, k.marge = vals
            if modele:
                a.deposer("exports", "modele_dpgf.xlsx", modele.getvalue())
            c.dpgf = construire_dpgf(c)
            a.sauver()
            st.rerun()
    if not c.dpgf:
        return
    total = sum(p.total or 0 for p in c.dpgf)
    cols = st.columns(3)
    carte(cols[0], "Total HT des postes chiffrés", f"{total:,.2f} €".replace(",", " "), "quantité × PU de vente")
    carte(cols[1], "Postes sans prix", str(sum(p.prix_unitaire_vente is None for p in c.dpgf)), "à chiffrer ou justifier")
    carte(cols[2], "Postes avec alerte", str(sum(bool(p.alertes) for p in c.dpgf)))
    st.write("")
    df = pd.DataFrame([{"id": p.ligne_quantite_id, "Lot": p.lot, "N°": p.code, "Désignation": p.designation, "Unité": p.unite,
                        "Qté": p.quantite, "Prix achat": p.prix_materiel, "Fournisseur": p.fournisseur,
                        "Temps pose h/u": p.temps_pose_h, "PU vente": p.prix_unitaire_vente, "Total": p.total,
                        "Alertes": " ; ".join(p.alertes)} for p in c.dpgf])
    df2 = st.data_editor(df, column_config={"id": None, "Prix achat": st.column_config.NumberColumn(format="%.2f €"),
                                            "PU vente": st.column_config.NumberColumn(format="%.2f €"),
                                            "Total": st.column_config.NumberColumn(format="%.2f €")},
                         hide_index=True, width="stretch", key=f"dpgf_{a.id}",
                         disabled=["Lot", "N°", "Désignation", "Unité", "Qté", "Fournisseur", "PU vente", "Total", "Alertes"])
    if st.button("Recalculer avec les temps de pose et valider la DPGF", icon=":material/calculate:"):
        for p, (_, x) in zip(c.dpgf, df2.iterrows()):
            p.temps_pose_h = None if pd.isna(x["Temps pose h/u"]) else float(x["Temps pose h/u"])
        saisis = {x["id"]: float(x["Prix achat"]) for _, x in df2.iterrows() if not pd.isna(x["Prix achat"])}
        temps = {p.ligne_quantite_id: p.temps_pose_h for p in c.dpgf}
        c.dpgf = construire_dpgf(c)
        for p in c.dpgf:
            p.temps_pose_h = temps.get(p.ligne_quantite_id, p.temps_pose_h)
            if p.prix_materiel is None and p.ligne_quantite_id in saisis:
                p.prix_materiel = saisis[p.ligne_quantite_id]
                p.alertes = [x for x in p.alertes if x != "Poste sans prix fournisseur" and "à choisir" not in x] + ["Prix saisi à la main"]
            p.prix_unitaire_vente = _prix_vente(p.prix_materiel, p.temps_pose_h, c)
            p.total = round(p.prix_unitaire_vente * p.quantite, 2) if p.prix_unitaire_vente is not None and p.quantite else None
        a.valider("dpgf", "dpgf", utilisateur(), role=role())
        revalide(c, "DPGF")
        a.sauver()
        st.rerun()
    bouton_export(a, "Générer la DPGF (Excel, avec feuille Suivi fournisseurs)", "DPGF.xlsx")
    suivant("memoire", "mémoire technique")


# --- Mémoire technique ------------------------------------------------------------------------------
def page_memoire() -> None:
    a = affaire_courante()
    c = a.c
    entete("Mémoire technique par consultation", "Un mémoire commun à plusieurs lots, ou un mémoire distinct par lot", a=a)
    lots = [l.numero for l in c.lots]
    retenus = [l.numero for l in c.lots if l.retenu] or lots
    with st.container(border=True):
        st.markdown("**1. Quel type de mémoire technique préparer ?**")
        m1, m2, m3 = st.columns([2, 2, 1])
        modes = {"commun": "Un seul mémoire pour plusieurs lots", "par_lot": "Un mémoire distinct par lot"}
        mode = m1.radio("Type", list(modes), format_func=modes.get, index=list(modes).index(c.mode_memoire) if c.mode_memoire else None,
                        label_visibility="collapsed")
        choix_lots = m2.multiselect("Lots concernés", lots, default=[x for x in (c.lots_memoire or retenus) if x in lots],
                                    format_func=lambda n: f"Lot {n}")
        m3.write("")
        if m3.button("Appliquer", type="primary", disabled=not mode or not choix_lots):
            preparer_memoires(c, mode, choix_lots)
            a.sauver()
            st.rerun()
        if not c.mode_memoire:
            st.caption("Sélectionne l'un des deux modes pour continuer. Le choix est propre à cette consultation ; "
                       "les textes déjà rédigés sont conservés si tu changes d'avis.")
    if not c.mode_memoire:
        bibliotheque_historique()
        return
    if not c.trame_memoire:
        st.info("Pas de trame : critères non trouvés dans le RC.")
        return
    lot_affiche = None
    if c.mode_memoire == "par_lot":
        lot_affiche = st.segmented_control("Mémoire du lot", c.lots_memoire, default=c.lots_memoire[0] if c.lots_memoire else None,
                                           format_func=lambda n: f"Lot {n}")
    sections = sections_memoire(c, lot_affiche)
    vigilance = [x for x in points_memoire(c) if lot_affiche is None or x.lot in (None, lot_affiche)]
    if vigilance:
        with st.expander(f"Points de vigilance à intégrer ({len(vigilance)})", icon=":material/priority_high:",
                         expanded=any(x.statut in OUVERTS for x in vigilance)):
            st.caption("Cochés « mémoire » dans le registre de l'analyse : à traiter dans les chapitres (moyens, méthodes, coordination), "
                       "pas seulement à recopier. Passe-les en « Traité » dans le registre une fois intégrés.")
            for x in vigilance:
                src = f" · {x.source}{' p. ' + x.page if x.page else ''}" if x.source else ""
                st.markdown(f"- **{x.intitule}**{src} · _{x.statut}_" + (f" · {x.action}" if x.action else ""))
    n_val = sum(s.statut == StatutSection.VALIDE for s in sections)
    st.progress(n_val / max(len(sections), 1), text=f"{n_val} section(s) validée(s) sur {len(sections)}")
    if c.cadre_reponse:
        st.success(f"Plan repris du cadre de réponse imposé par l'acheteur ({ref(c.cadre_reponse)}), dans son ordre.", icon=":material/rule:")
    else:
        st.caption("Pas de cadre de réponse trouvé dans le DCE : plan issu des critères de notation du RC.")
    st.caption("Pour chaque section : reprendre un passage d'archive ou demander un brouillon à l'IA, l'adapter, puis valider. "
               "Les [À COMPLÉTER] signalent ce que l'outil n'a pas pu tirer des archives (équipe, moyens, planning…).")
    ia_active = moteur_choisi() != "aucun"
    for s in sections:
        pond = f" · {s.ponderation:g} %" if s.ponderation else ""
        propre = f" · lot {s.lot}" if s.lot and c.mode_memoire == "commun" else ""
        icone = {StatutSection.VALIDE: ":material/check_circle:", StatutSection.PROPOSE: ":material/edit_note:"}.get(s.statut, ":material/radio_button_unchecked:")
        with st.expander(f"{s.titre}{pond}{propre}", icon=icone):
            if s.note:
                st.info(s.note)
            gauche, droite = st.columns([1, 1], gap="large")
            with gauche:
                texte = st.text_area("Texte de la section", s.contenu or "", height=320, key=f"txt_{s.id}")
                if s.sources_reprises:
                    st.caption("Sources reprises : " + " ; ".join(ref(x) for x in s.sources_reprises))
                b1, b2 = st.columns(2)
                if b1.button("Enregistrer", key=f"sv_{s.id}", icon=":material/save:", width="stretch"):
                    s.contenu = texte or None
                    if texte and s.statut == StatutSection.A_COMPLETER:
                        s.statut = StatutSection.PROPOSE
                    a.sauver()
                    st.toast("Section enregistrée.")
                if b2.button("Valider la section", key=f"va_{s.id}", disabled=not texte or "[À COMPLÉTER" in texte,
                             type="primary", icon=":material/task_alt:", width="stretch", help="Impossible tant qu'il reste des [À COMPLÉTER]."):
                    s.contenu, s.statut = texte, StatutSection.VALIDE
                    a.sauver()
                    st.rerun()
            with droite:
                onglets = st.tabs(["Archives", "Brouillon IA"])
                with onglets[0]:
                    if not s.passages_proposes:
                        st.caption("Aucun passage d'archive proposé.")
                    for i, pa in enumerate(s.passages_proposes[:5]):
                        st.markdown(f"**{pa.affaire}** · {Path(pa.source.fichier).name}, p. {pa.source.page}")
                        st.markdown(f'<div class="sie-citation">{pa.texte}</div>', unsafe_allow_html=True)
                        if st.button("Reprendre ce passage", key=f"pr_{s.id}_{i}", icon=":material/add:"):
                            s.contenu = (s.contenu + "\n\n" if s.contenu else "") + pa.texte
                            if pa.source not in s.sources_reprises:
                                s.sources_reprises.append(pa.source)
                            s.statut = StatutSection.PROPOSE if s.statut == StatutSection.A_COMPLETER else s.statut
                            a.sauver()
                            st.session_state.pop(f"txt_{s.id}", None)
                            st.rerun()
                with onglets[1]:
                    brouillons = [x for x in c.reponses_ia if x.type == "memoire" and x.question == f"Brouillon : {s.titre}"]
                    if not ia_active:
                        st.caption("IA désactivée (voir « Configurer l'IA » dans la barre latérale).")
                    elif st.button("Rédiger un brouillon", key=f"ia_{s.id}", icon=":material/auto_awesome:"):
                        moteur = moteur_pour(a)
                        if moteur and appeler_ia(a, ia.brouillon_section, c, s, moteur):
                            st.rerun()
                    if brouillons:
                        x = brouillons[-1]
                        st.markdown(f'<div class="sie-citation">{x.texte}</div>', unsafe_allow_html=True)
                        st.caption(f"{x.moteur} · à partir de {len(x.citations)} passage(s) d'archives · {x.cout_usd:.3f} $")
                        if st.button("Reprendre ce brouillon", key=f"rb_{s.id}", icon=":material/add:"):
                            s.contenu = (s.contenu + "\n\n" if s.contenu else "") + x.texte
                            s.statut = StatutSection.PROPOSE if s.statut == StatutSection.A_COMPLETER else s.statut
                            a.sauver()
                            st.session_state.pop(f"txt_{s.id}", None)
                            st.rerun()
    st.subheader("Références d'affaires comparables")
    if not c.references:
        st.caption("Aucune affaire comparable dans les archives indexées.")
    else:
        st.caption("Proposées d'après les archives SIE. Coche celles à citer : rien n'est ajouté au mémoire sans ton choix.")
        refs_df = st.data_editor(pd.DataFrame([{"Citer": bool(x.retenue), "Affaire": x.affaire, "Année": x.annee, "Type": x.type or "",
                                                "Activité": x.activite or "", "Résultat": x.resultat or "", "Proximité": x.score}
                                               for x in c.references]),
                                 disabled=["Affaire", "Année", "Type", "Activité", "Résultat", "Proximité"], hide_index=True,
                                 width="stretch", key=f"refs_{a.id}")
        if st.button("Enregistrer le choix des références", icon=":material/save:"):
            for x, (_, y) in zip(c.references, refs_df.iterrows()):
                x.retenue = bool(y["Citer"])
            a.sauver()
            st.toast("Références enregistrées.")
    st.subheader("Pièces jointes au mémoire")
    st.caption("Équipe, planning, notes de calcul, synoptiques produits hors outil par le bureau d'études.")
    pj = st.file_uploader("Ajouter des pièces jointes", accept_multiple_files=True, key=f"pj_{a.id}")
    if pj and st.button("Joindre", type="primary", icon=":material/attach_file:"):
        for f in pj:
            a.deposer("pieces_reponse", f.name, f.getvalue())
            if f.name not in c.pieces_jointes_memoire:
                c.pieces_jointes_memoire.append(f.name)
        a.sauver()
        st.rerun()
    for nom in c.pieces_jointes_memoire:
        st.markdown(f":material/attach_file: {nom}")
    manques = complements_a_demander(c)
    st.subheader(f"Compléments à fournir ({len(manques)})")
    if manques:
        st.dataframe(pd.DataFrame([{"Section": x, "À fournir": y} for x, y in manques]), hide_index=True, width="stretch")
        bouton_export(a, "Télécharger la liste (Word)", "Complements_memoire.docx")
    else:
        st.success("Rien à compléter.", icon=":material/check_circle:")
    toutes = sections_memoire(c)
    if toutes and all(s.statut == StatutSection.VALIDE for s in toutes) and not a.est_valide("memoire"):
        a.valider("memoire", "memoire", utilisateur(), role=role())
        revalide(c, "mémoire technique")
        a.sauver()
    if c.mode_memoire == "par_lot":
        cols = st.columns(max(len(c.lots_memoire), 1))
        for col, lot in zip(cols, c.lots_memoire):
            with col:
                bouton_export(a, f"Mémoire du lot {lot} (Word)", f"Memoire_technique_lot{lot}.docx")
    else:
        bouton_export(a, "Télécharger le mémoire (Word)", "Memoire_technique.docx")
    bibliotheque_historique()
    suivant("documents", "documents")


def bibliotheque_historique() -> None:
    with st.expander("Bibliothèque historique SIE : anciens mémoires sur le serveur", icon=":material/lock:"):
        r = refs()
        cfg = r.bibliotheque
        st.caption("Les mémoires restent sur le partage d'origine, lu en lecture seule par le compte de service de l'outil. "
                   "L'index est stocké sur le serveur SIE. Le moteur RAG du noyau IA est étudié à part ; cette version utilise "
                   "la recherche par mots-clés existante.")
        autorise = role() in ("Administrateur", "Direction", "Responsable technique")
        with st.form("cfg_biblio"):
            chemin = st.text_input("Chemin du partage documentaire", cfg.chemin, placeholder=r"\\serveur\partage\memoires",
                                   help="De préférence un chemin UNC plutôt qu'une lettre de lecteur liée à une session.")
            c1, c2, c3 = st.columns(3)
            heure = c1.text_input("Synchronisation quotidienne à", cfg.heure_synchro, help="À planifier sur le serveur : "
                                  "python -m sie_tender synchroniser")
            sous = c2.checkbox("Inclure tous les sous-dossiers", cfg.sous_dossiers)
            valides = c3.checkbox("Mémoires validés seulement", cfg.valides_seulement)
            motif = st.text_input("Règle provisoire « mémoire validé » (expression sur le chemin)", cfg.motif_valides,
                                  help="À remplacer par la règle définie avec SIE (dossier dédié, nommage, statut).")
            if st.form_submit_button("Enregistrer la configuration", disabled=not autorise):
                cfg.chemin, cfg.heure_synchro, cfg.sous_dossiers, cfg.valides_seulement, cfg.motif_valides = chemin.strip(), heure, sous, valides, motif
                sauver_refs(r)
                st.rerun()
        etat = (f"Dernière synchronisation : {cfg.derniere_synchro:%d/%m/%Y %H:%M} · {cfg.dernier_resultat}" if cfg.derniere_synchro
                else "Jamais synchronisée.")
        st.caption(f"{etat} · {cfg.nb_fichiers} fichier(s) suivi(s)." + (f" Erreurs : {'; '.join(cfg.erreurs)}" if cfg.erreurs else ""))
        if st.button("Synchroniser maintenant", icon=":material/sync:", disabled=not autorise or not cfg.chemin,
                     help=None if autorise else "Réservé aux profils Administrateur, Direction et Responsable technique."):
            with st.spinner("Synchronisation incrémentale…"):
                msg = synchroniser(cfg, INDEX)
            sauver_refs(r)
            st.toast(msg)
            st.rerun()
        b = biblio()
        if b:
            st.caption(f"Index : {len({d.affaire for d in b.documents})} affaires, {len(b.documents)} documents, {len(b.morceaux)} passages.")
            q = st.text_input("Rechercher dans les anciens mémoires", placeholder="Ex. : intervention en site occupé école")
            if q:
                st.caption("Affaires les plus proches : " + ", ".join(f"{x} ({s})" for x, s in b.affaires_similaires(q)))
                for p in b.rechercher(q):
                    st.markdown(f"**{p.affaire}** · {ref(p.source)} · score {p.score}")
                    st.markdown(f'<div class="sie-citation">{p.texte}</div>', unsafe_allow_html=True)


# --- Documents ---------------------------------------------------------------------------------------
def page_documents() -> None:
    a = affaire_courante()
    c = a.c
    entete("Documents et complétude", "Pièces du dossier de réponse : reçues, puis validées par SIE", a=a)
    if synchroniser_pieces(c):
        a.sauver()
    pr = c.pieces_reponse
    k = st.columns(3)
    carte(k[0], "Pièces dans la liste", str(len(pr)))
    carte(k[1], "Présentes", f"{sum(p.recu for p in pr)} / {len(pr)}")
    carte(k[2], "Présentes et validées", f"{sum(p.recu and p.valide for p in pr)} / {len(pr)}")
    st.write("")
    with st.expander("Déposer des pièces de réponse", icon=":material/upload_file:"):
        fichiers = st.file_uploader("Attestations, AE signé, planning, fiches techniques…", accept_multiple_files=True, key=f"pr_{a.id}")
        if st.button("Déposer", disabled=not fichiers, type="primary"):
            for f in fichiers:
                a.deposer("pieces_reponse", f.name, f.getvalue())
            st.rerun()
    deposes = sorted(p.name for p in a.dossier("pieces_reponse").iterdir())
    filtre = st.segmented_control("Étape", ["Toutes", "Candidature", "Offre"], default="Toutes")
    vues = [p for p in pr if filtre in (None, "Toutes") or p.etape == filtre.lower()]
    df = pd.DataFrame([{"id": p.id, "Étape": p.etape, "Lot": p.lot or "", "Pièce": p.intitule, "Caractère": p.obligatoire,
                        "Source": p.source or "", "Fichier": p.fichier, "Reçu": p.recu, "Validé": p.valide} for p in vues],
                      columns=["id", "Étape", "Lot", "Pièce", "Caractère", "Source", "Fichier", "Reçu", "Validé"])
    df2 = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic", key=f"docs_{a.id}_{filtre}",
                         column_config={"id": None, "Pièce": st.column_config.TextColumn(width="large", required=True),
                                        "Étape": st.column_config.SelectboxColumn(options=["candidature", "offre"], required=True),
                                        "Caractère": st.column_config.SelectboxColumn(options=["Obligatoire", "À vérifier"]),
                                        "Fichier": st.column_config.SelectboxColumn(options=deposes, help="Fichier déposé correspondant"),
                                        "Source": st.column_config.TextColumn(disabled=True)})
    if st.button("Enregistrer la liste", type="primary", icon=":material/save:"):
        par_id = {p.id: p for p in pr}
        caches = [p for p in pr if p not in vues]
        nouvelles = []
        for _, x in df2.iterrows():
            if not str(x["Pièce"] or "").strip():
                continue
            p = par_id.get(x["id"]) if isinstance(x["id"], str) else None
            p = p or PieceReponse(intitule="", source="ajoutée par SIE")
            fichier = x["Fichier"] if isinstance(x["Fichier"], str) and x["Fichier"] else None
            if fichier and fichier != p.fichier:
                p.version += 1
            p.intitule, p.etape, p.lot = str(x["Pièce"]).strip(), x["Étape"] or "offre", (str(x["Lot"]).strip() or None) if x["Lot"] else None
            p.obligatoire, p.fichier = x["Caractère"] or "À vérifier", fichier
            p.recu = bool(x["Reçu"]) or bool(fichier)
            p.valide = bool(x["Validé"]) and p.recu
            nouvelles.append(p)
        c.pieces_reponse = nouvelles + caches
        a.sauver()
        st.toast("Liste enregistrée.", icon=":material/check_circle:")
        st.rerun()
    st.caption("« À vérifier » : le caractère obligatoire dépend du règlement ou des choix de candidature ; il doit être confirmé "
               "dans les pièces de la consultation, le logiciel ne devine pas. Une pièce ne peut être validée sans être reçue.")
    suivant("controle", "contrôle et export")


# --- Contrôle & export --------------------------------------------------------------------------------
def page_controle() -> None:
    a = affaire_courante()
    c = a.c
    entete("Contrôle final et export", "Dernier contrôle avant un dossier prêt pour validation et dépôt manuel par SIE", a=a)
    constats = controler(a)
    nb = {g: sum(k.gravite == g for k in constats) for g in ("bloquant", "a_verifier")}
    pr = c.pieces_reponse
    k = st.columns(4)
    carte(k[0], "Anomalies bloquantes", str(nb["bloquant"]))
    carte(k[1], "Points à vérifier", str(nb["a_verifier"]))
    carte(k[2], "Pièces validées", f"{sum(p.valide for p in pr)} / {len(pr)}")
    carte(k[3], "Validations internes", f"{sum(bool(c.validations_finales.get(x)) for x in VALIDATIONS_FINALES)} / {len(VALIDATIONS_FINALES)}")
    st.write("")
    gauche, droite = st.columns([3, 2], gap="large")
    with gauche:
        st.subheader("Anomalies")
        if not constats:
            st.success("Aucun manque détecté.", icon=":material/check_circle:")
        st.dataframe(pd.DataFrame([{"Gravité": {"bloquant": "🔴 bloquant", "a_verifier": "🟠 à vérifier"}.get(x.gravite, "🔵 info"),
                                    "Objet": x.objet, "Constat": x.message} for x in
                                   sorted(constats, key=lambda x: {"bloquant": 0, "a_verifier": 1}.get(x.gravite, 2))]),
                     hide_index=True, width="stretch", column_config={"Constat": st.column_config.TextColumn(width="large")})
        st.download_button("Exporter le rapport (CSV)", pd.DataFrame([x.model_dump() for x in constats]).to_csv(index=False, sep=";").encode("utf-8-sig"),
                           file_name=f"Rapport_controle_{c.fiche.reference.valeur or a.id}.csv", icon=":material/download:")
    with droite:
        st.subheader("Validations humaines SIE")
        for cle, libelle in VALIDATIONS_FINALES.items():
            v = c.validations_finales.get(cle)
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"**{libelle}**  \n{('Approuvée ' + v) if v else 'Non approuvée'}")
            if c2.button("Retirer" if v else "Approuver", key=f"vf_{cle}", width="stretch"):
                c.validations_finales[cle] = None if v else f"par {utilisateur()} ({role()}) le {maintenant()}"
                if not v:
                    a.valider(f"final_{cle}", f"final_{cle}", utilisateur(), role=role())
                c.pret_a_deposer = None
                a.sauver()
                st.rerun()
        st.subheader("Statut du dossier")
        ok, raison = peut_declarer_pret(c, constats)
        if c.pret_a_deposer:
            st.success(f"Prêt pour dépôt : {c.pret_a_deposer}", icon=":material/verified:")
        else:
            st.caption(raison)
        if nb["bloquant"]:
            with st.popover("Dérogation formalisée", icon=":material/gavel:", disabled=role() != "Direction"):
                st.caption("Réservée à la direction : justifie pourquoi le dossier peut être déclaré prêt malgré les anomalies bloquantes.")
                motif = st.text_area("Motif", c.derogation or "")
                if st.button("Enregistrer la dérogation", type="primary", disabled=not motif.strip()):
                    c.derogation = f"{motif.strip()} (par {utilisateur()}, {role()}, le {maintenant()})"
                    a.valider("derogation", "derogation", utilisateur(), commentaire=motif.strip(), role=role())
                    a.sauver()
                    st.rerun()
        if not c.pret_a_deposer and st.button("Déclarer « prêt à déposer »", type="primary", disabled=not ok, icon=":material/verified:"):
            c.pret_a_deposer = f"par {utilisateur()} ({role()}) le {maintenant()}"
            a.valider("pret", "pret_a_deposer", utilisateur(), commentaire=raison, role=role())
            revalide(c, "contrôle final")
            a.sauver()
            st.rerun()
        st.caption("Aucun dépôt automatique : le dépôt sur la plateforme de l'acheteur reste fait par SIE.")
    st.subheader("Structure de sortie")
    retenus = [l.numero for l in c.lots if l.retenu]
    nom = f"{c.fiche.reference.valeur or a.id}_Dossier_Offre/"
    arbo = [nom, "├── 00_Liste_de_controle.md", "├── 01_Candidature/"]
    arbo += [f"│   ├── {p.fichier or p.intitule + ' (manquante)'}" for p in pr if p.etape == "candidature"]
    arbo += ["├── 02_Offre/", "│   ├── DPGF.xlsx"]
    if c.mode_memoire == "par_lot":
        arbo += [f"│   ├── Lot_{l}/Memoire_technique_lot{l}.docx" for l in c.lots_memoire]
    else:
        arbo += ["│   ├── Memoire_technique.docx"]
    arbo += [f"│   ├── {('Lot_' + p.lot + '/') if p.lot and len(retenus) > 1 else ''}{p.fichier or p.intitule + ' (manquante)'}"
             for p in pr if p.etape == "offre" and p.intitule not in ("DPGF", "Mémoire technique") and not p.intitule.startswith(("DPGF du", "Mémoire technique du"))]
    arbo += ["├── 03_Annexes/", "└── 04_Travail_interne_ne_pas_deposer/Analyse_DCE.xlsx"]
    st.code("\n".join(arbo), language=None)
    if st.button("Préparer le dossier (ZIP)", icon=":material/folder_zip:"):
        with st.spinner("Préparation du dossier…"):
            chemin = exporter_dossier(a)
            a.sauver()
        st.session_state["zip"] = str(chemin)
    if st.session_state.get("zip") and Path(st.session_state["zip"]).exists() and a.id in st.session_state["zip"]:
        z = Path(st.session_state["zip"])
        st.download_button("Télécharger le dossier", z.read_bytes(), file_name=z.name, icon=":material/download:")
        st.caption("Dossier destiné à la relecture et au dépôt manuel par SIE. Le dossier 04 est interne : ne pas le déposer.")
    with st.expander("Journal des validations", icon=":material/history:"):
        historique(a)


# --- Paramètres ------------------------------------------------------------------------------------
def page_parametres() -> None:
    entete("Paramètres et référentiels SIE", "Fournisseurs, utilisateurs et personnes disponibles pour les visites de site")
    r = refs()
    st.caption("Référentiels communs à toutes les consultations, enregistrés sur le serveur de l'outil. Aucun mot de passe n'est stocké ; "
               "l'authentification Windows / Active Directory est à brancher en production.")
    onglets = st.tabs([f"Fournisseurs ({len(r.fournisseurs)})", f"Utilisateurs ({len(r.utilisateurs)})",
                       f"Personnes pour visites ({len(r.visites)})"])
    with onglets[0]:
        st.caption("Plusieurs contacts par société ; spécialités utilisées pour proposer des fournisseurs dans les demandes de prix. "
                   "Une fiche déjà sollicitée se désactive au lieu d'être supprimée.")
        df = pd.DataFrame([{"id": f.id, "Raison sociale": f.raison_sociale, "Spécialités": ", ".join(f.specialites), "Adresse": f.adresse,
                            "Téléphone": f.telephone, "E-mail": f.email, "Actif": f.actif, "Consultations": len(f.historique)}
                           for f in r.fournisseurs],
                          columns=["id", "Raison sociale", "Spécialités", "Adresse", "Téléphone", "E-mail", "Actif", "Consultations"])
        df2 = st.data_editor(df, hide_index=True, width="stretch", num_rows="dynamic", key="ref_fourn",
                             column_config={"id": None, "Raison sociale": st.column_config.TextColumn(required=True),
                                            "Spécialités": st.column_config.TextColumn(help="Séparées par des virgules : " + ", ".join(SPECIALITES)),
                                            "Consultations": st.column_config.NumberColumn(disabled=True)})
        if st.button("Enregistrer les fournisseurs", type="primary", icon=":material/save:"):
            par_id = {f.id: f for f in r.fournisseurs}
            gardes, avertis = [], []
            for _, x in df2.iterrows():
                if not str(x["Raison sociale"] or "").strip():
                    continue
                f = par_id.pop(x["id"], None) if isinstance(x["id"], str) else None
                f = f or Fournisseur(raison_sociale="")
                f.raison_sociale = str(x["Raison sociale"]).strip()
                f.specialites = [s.strip() for s in str(x["Spécialités"] or "").split(",") if s.strip()]
                f.adresse, f.telephone, f.email = str(x["Adresse"] or ""), str(x["Téléphone"] or ""), str(x["E-mail"] or "").strip()
                f.actif = bool(x["Actif"]) if x["Actif"] is not None else True
                if f.email and not email_valide(f.email):
                    avertis.append(f"{f.raison_sociale} : e-mail « {f.email} » à vérifier")
                gardes.append(f)
            for f in par_id.values():  # lignes supprimées
                if f.historique:
                    f.actif = False
                    gardes.append(f)
                    avertis.append(f"{f.raison_sociale} déjà sollicité : désactivé au lieu d'être supprimé")
            r.fournisseurs = gardes
            sauver_refs(r)
            for m in avertis:
                st.warning(m)
            st.toast("Fournisseurs enregistrés.", icon=":material/check_circle:")
        if r.fournisseurs:
            st.markdown("**Contacts**")
            f = st.selectbox("Société", r.fournisseurs, format_func=lambda f: f.raison_sociale, key="ref_soc")
            dc = pd.DataFrame([{"id": x.id, "Nom": x.nom, "Prénom": x.prenom, "Fonction": x.fonction, "Téléphone": x.telephone,
                                "E-mail": x.email, "Domaine": x.domaine} for x in f.contacts],
                              columns=["id", "Nom", "Prénom", "Fonction", "Téléphone", "E-mail", "Domaine"])
            dc2 = st.data_editor(dc, hide_index=True, width="stretch", num_rows="dynamic", key=f"ref_ct_{f.id}", column_config={"id": None})
            if st.button("Enregistrer les contacts", icon=":material/save:"):
                par_id = {x.id: x for x in f.contacts}
                f.contacts = []
                for _, x in dc2.iterrows():
                    if not any(str(x[k] or "").strip() for k in ("Nom", "Prénom", "E-mail")):
                        continue
                    ct = par_id.get(x["id"]) if isinstance(x["id"], str) else None
                    ct = ct or Contact()
                    ct.nom, ct.prenom, ct.fonction = str(x["Nom"] or ""), str(x["Prénom"] or ""), str(x["Fonction"] or "")
                    ct.telephone, ct.email, ct.domaine = str(x["Téléphone"] or ""), str(x["E-mail"] or "").strip(), str(x["Domaine"] or "")
                    if ct.email and not email_valide(ct.email):
                        st.warning(f"E-mail « {ct.email} » à vérifier")
                    f.contacts.append(ct)
                sauver_refs(r)
                st.toast("Contacts enregistrés.", icon=":material/check_circle:")
    with onglets[1]:
        admin = role() in ("Administrateur", "Direction")
        if not admin:
            st.info("Seuls les profils Administrateur et Direction modifient les comptes et les rôles.", icon=":material/lock:")
        du = pd.DataFrame([{"id": u.id, "Nom": u.nom, "Prénom": u.prenom, "Société": u.societe, "Identifiant": u.identifiant,
                            "E-mail": u.email, "Rôle": u.role, "Actif": u.actif} for u in r.utilisateurs],
                          columns=["id", "Nom", "Prénom", "Société", "Identifiant", "E-mail", "Rôle", "Actif"])
        du2 = st.data_editor(du, hide_index=True, width="stretch", num_rows="dynamic" if admin else "fixed", key="ref_users",
                             disabled=not admin, column_config={"id": None, "Rôle": st.column_config.SelectboxColumn(options=ROLES)})
        if admin and st.button("Enregistrer les utilisateurs", type="primary", icon=":material/save:"):
            par_id = {u.id: u for u in r.utilisateurs}
            r.utilisateurs = []
            for _, x in du2.iterrows():
                if not any(str(x[k] or "").strip() for k in ("Nom", "Prénom", "Identifiant")):
                    continue
                u = par_id.get(x["id"]) if isinstance(x["id"], str) else None
                u = u or Utilisateur()
                u.nom, u.prenom, u.societe = str(x["Nom"] or ""), str(x["Prénom"] or ""), str(x["Société"] or "SIE")
                u.identifiant, u.email = str(x["Identifiant"] or ""), str(x["E-mail"] or "").strip()
                u.role, u.actif = x["Rôle"] or ROLES[1], bool(x["Actif"]) if x["Actif"] is not None else True
                r.utilisateurs.append(u)
            sauver_refs(r)
            st.toast("Utilisateurs enregistrés.", icon=":material/check_circle:")
    with onglets[2]:
        dv = pd.DataFrame([{"id": p.id, "Nom": p.nom, "Prénom": p.prenom, "Fonction": p.fonction, "Téléphone": p.telephone,
                            "Secteur": p.secteur, "Disponibilité": p.disponibilite, "Actif": p.actif} for p in r.visites],
                          columns=["id", "Nom", "Prénom", "Fonction", "Téléphone", "Secteur", "Disponibilité", "Actif"])
        dv2 = st.data_editor(dv, hide_index=True, width="stretch", num_rows="dynamic", key="ref_visites", column_config={"id": None})
        if st.button("Enregistrer les personnes", type="primary", icon=":material/save:"):
            par_id = {p.id: p for p in r.visites}
            r.visites = []
            for _, x in dv2.iterrows():
                if not any(str(x[k] or "").strip() for k in ("Nom", "Prénom")):
                    continue
                p = par_id.get(x["id"]) if isinstance(x["id"], str) else None
                p = p or PersonneVisite()
                for champ, col in (("nom", "Nom"), ("prenom", "Prénom"), ("fonction", "Fonction"), ("telephone", "Téléphone"),
                                   ("secteur", "Secteur"), ("disponibilite", "Disponibilité")):
                    setattr(p, champ, str(x[col] or ""))
                p.actif = bool(x["Actif"]) if x["Actif"] is not None else True
                r.visites.append(p)
            sauver_refs(r)
            st.toast("Personnes enregistrées.", icon=":material/check_circle:")


# --- Navigation et barre latérale -------------------------------------------------------------
PAGES = {
    "tableau": st.Page(page_tableau, title="Tableau de bord", icon=":material/dashboard:", url_path="tableau", default=True),
    "consultations": st.Page(page_consultations, title="Consultations", icon=":material/folder_open:", url_path="consultations"),
    "chargement": st.Page(page_chargement, title="Chargement du DCE", icon=":material/upload:", url_path="chargement"),
    "analyse": st.Page(page_analyse, title="Analyse du DCE", icon=":material/search:", url_path="analyse"),
    "fiche": st.Page(page_fiche, title="Fiche de renseignement", icon=":material/fact_check:", url_path="fiche"),
    "metres": st.Page(page_metres, title="Décomptes & métrés", icon=":material/straighten:", url_path="metres"),
    "demandes": st.Page(page_demandes, title="Demandes de prix fournisseurs", icon=":material/mail:", url_path="demandes"),
    "dpgf": st.Page(page_dpgf, title="Chiffrage / DPGF", icon=":material/request_quote:", url_path="dpgf"),
    "memoire": st.Page(page_memoire, title="Mémoire technique", icon=":material/description:", url_path="memoire"),
    "documents": st.Page(page_documents, title="Documents", icon=":material/inventory_2:", url_path="documents"),
    "controle": st.Page(page_controle, title="Contrôle & export", icon=":material/verified:", url_path="controle"),
    "parametres": st.Page(page_parametres, title="Paramètres", icon=":material/settings:", url_path="parametres"),
}
nav = st.navigation({"Espace de travail": list(PAGES.values())[:-1], "Référentiels": [PAGES["parametres"]]})


def _changer_affaire() -> None:
    st.session_state["affaire"] = st.session_state.get("_selecteur")


with st.sidebar:
    st.markdown("### SIE Tender Intelligence")
    actives = resumes()
    ids = [x["id"] for x in actives]
    courant = st.session_state.get("affaire")
    if courant and courant not in ids and (ESPACE.racine / courant / "consultation.json").exists():
        ids.insert(0, courant)  # consultation archivée ouverte depuis la liste
        actives.insert(0, _resume(courant, _mtime(ESPACE.racine / courant / "consultation.json")))
    libelles = {x["id"]: f"{x['reference'] or x['id']} · {x['titre'][:48]}" for x in actives}
    st.session_state["_selecteur"] = courant if courant in ids else None
    st.selectbox("Consultation / DCE à traiter", ids, format_func=libelles.get, key="_selecteur", on_change=_changer_affaire,
                 index=None, placeholder="— Choisir une consultation —",
                 help="Toutes les rubriques travaillent sur cette consultation. Les données ne se mélangent pas entre dossiers. "
                      "Pense à enregistrer avant d'en changer.")
    if courant in libelles:
        r_ = next(x for x in actives if x["id"] == courant)
        st.caption(f"{r_['statut']} · remise {jours_restants(r_['limite'])} · lots retenus : {r_['retenus'] or '—'}")
    st.divider()
    st.text_input("Ton nom (pour les validations)", key="utilisateur", placeholder="Prénom Nom")
    st.selectbox("Rôle", ROLES, key="role", index=1, help="La décision GO / NO GO est réservée à la direction.")
    with st.expander("Configurer l'IA", icon=":material/auto_awesome:"):
        dispo = _moteurs()
        st.radio("Moteur", dispo, format_func=lambda m: NOMS_MOTEURS.get(m, m), key="moteur", index=dispo.index(moteur_choisi()))
        if "openai" not in dispo and "anthropic" not in dispo:
            st.caption("LLM par API (accord SIE) : l'administrateur renseigne OPENAI_API_KEY (ou ANTHROPIC_API_KEY) et "
                       "SIE_LLM_EXTERNE_AUTORISE=oui sur le serveur.")
        else:
            st.caption("API activée sur ce serveur : les pages utiles du DCE et des archives sont envoyées au fournisseur d'IA.")
        if "ollama" not in dispo:
            st.caption("Modèle local : installer Ollama sur le serveur.")
        if st.session_state.get("cout_ia"):
            st.caption(f"Coût IA de cette session : {st.session_state['cout_ia']:.3f} $")
    st.caption(f"Archives : {'indexées' if INDEX.exists() else 'non indexées'}")

if st.session_state.get("_page_test"):  # tests automatiques : page appelée directement
    {"tableau": page_tableau, "consultations": page_consultations, "chargement": page_chargement, "analyse": page_analyse,
     "fiche": page_fiche, "metres": page_metres, "demandes": page_demandes, "dpgf": page_dpgf, "memoire": page_memoire,
     "documents": page_documents, "controle": page_controle, "parametres": page_parametres}[st.session_state["_page_test"]]()
else:
    nav.run()
