"""Documents imprimables (PDF) produits localement : fiche de renseignement, synthèse du registre de vigilance.

Le PDF est construit avec PyMuPDF à partir d'un HTML simple : aucune bibliothèque ni service externe.
"""
from __future__ import annotations

from datetime import datetime
from html import escape
from pathlib import Path

from .export import LIBELLES, ref
from .models import Consultation, Statut

CSS = """
body {font-family: sans-serif; font-size: 9pt; color: #1b2733;}
h1 {font-size: 15pt; color: #12385c; margin-bottom: 2pt;}
h2 {font-size: 11pt; color: #12385c; margin-top: 10pt; margin-bottom: 3pt; border-bottom: 1px solid #9fb3c8;}
p.petit {font-size: 7.5pt; color: #5b6b7b;}
table {border-collapse: collapse; width: 100%;}
td, th {border: 1px solid #b8c4d0; padding: 2pt 4pt; vertical-align: top; text-align: left;}
th {background-color: #e8eef4;}
.verif {color: #b45309; font-weight: bold;}
"""

SECTIONS_FICHE = [
    ("1. Identification de l'appel d'offres", ["intitule", "reference", "objet", "procedure"]),
    ("2. Localisation du projet", ["lieu"]),
    ("3. Dates importantes", ["date_limite_remise", "date_limite_questions", "visite", "visite_obligatoire", "visite_sur_rdv",
                              "fiche_visite_signee", "contacts", "duree_validite_offres"]),
    ("4. Acteurs du projet", ["maitre_ouvrage", "maitre_oeuvre", "architecte", "coordonnateur_sps", "coordinateur_ssi", "intervenants"]),
]


def _html_vers_pdf(html: str, chemin: Path) -> Path:
    import pymupdf as fitz

    chemin = Path(chemin)
    story = fitz.Story(html=html, user_css=CSS)
    redacteur = fitz.DocumentWriter(str(chemin))
    page = fitz.paper_rect("a4")
    zone = page + (36, 36, -36, -40)
    plus = True
    while plus:
        dispositif = redacteur.begin_page(page)
        plus, _ = story.place(zone)
        story.draw(dispositif)
        redacteur.end_page()
    redacteur.close()
    return chemin


def _valeur(ch) -> str:
    if not ch.valeur:
        return '<span class="verif">À vérifier</span>'
    v = escape(ch.valeur).replace("\n", "<br/>")
    if ch.statut == Statut.A_VERIFIER:
        v += ' <span class="verif">(à vérifier)</span>'
    return v


def fiche_pdf(c: Consultation, chemin: str | Path) -> Path:
    f = c.fiche
    h = ["<h1>Fiche de renseignement - Analyse appel d'offres</h1>",
         f"<p class='petit'>Imprimée le {datetime.now():%d/%m/%Y %H:%M}. Les données non trouvées dans le DCE sont marquées « À vérifier ».</p>"]
    champs = f.champs()
    for titre, cles in SECTIONS_FICHE:
        h.append(f"<h2>{titre}</h2>")
        for k in cles:
            ch = champs[k]
            src = f" <span class='petit'>[{escape(ref(ch.source))}]</span>" if ch.source else ""
            h.append(f"<p><b>{escape(LIBELLES.get(k, k))} :</b> {_valeur(ch)}{src}</p>")
    h.append("<h2>5. Décomposition en lots (liste complète)</h2><table><tr><th>N°</th><th>Intitulé exact</th><th>Répondre</th></tr>")
    for l in c.lots:
        h.append(f"<tr><td>{escape(l.numero)}</td><td>{escape(l.intitule)}</td>"
                 f"<td>{ {True: 'Oui', False: 'Non', None: '(après GO)'}[l.retenu]}</td></tr>")
    if not c.lots:
        h.append("<tr><td colspan='3' class='verif'>Aucun lot saisi : à vérifier dans le RC.</td></tr>")
    h.append("</table>")
    dec = f.decision or "En attente de décision"
    qui = f" par {escape(f.decideur or '?')} le {f.date_decision:%d/%m/%Y}" if f.date_decision else ""
    h.append(f"<h2>6. Décision GO / NO GO de la direction</h2><p><b>{escape(dec)}</b>{qui}</p>")
    if f.historique_decisions:
        h.append("<p class='petit'>Historique : " + " ; ".join(escape(x) for x in f.historique_decisions) + "</p>")
    h.append("<h2>7. Responsable de visite et points de vigilance</h2>")
    h.append(f"<p><b>Responsable de la visite :</b> {escape(f.responsable_visite or 'À désigner')}</p>")
    h.append(f"<p><b>Date de la visite :</b> {escape(f.date_visite or 'À fixer')}</p>")
    if f.alertes:
        h.append("<ul>" + "".join(f"<li>{escape(a)}</li>" for a in f.alertes) + "</ul>")
    if f.commentaires:
        h.append(f"<p><b>Commentaires / validations SIE :</b><br/>{escape(f.commentaires).replace(chr(10), '<br/>')}</p>")
    return _html_vers_pdf("".join(h), Path(chemin))


def registre_pdf(c: Consultation, chemin: str | Path) -> Path:
    from .suivi import OUVERTS, synthese_registre

    s = synthese_registre(c)
    titre = escape(c.fiche.intitule.valeur or c.fiche.objet.valeur or c.id)
    h = [f"<h1>Registre des alertes et points de vigilance</h1><p>{titre}</p>",
         f"<p class='petit'>Imprimé le {datetime.now():%d/%m/%Y %H:%M}. {s['ouverts']} point(s) ouvert(s), "
         f"{s['bloquants']} bloquant(s), {s['memoire_ouverts']} à intégrer au mémoire. "
         "Chaque point est à vérifier dans le document source.</p>"]
    for groupe, filtre in (("Points ouverts", lambda a: a.statut in OUVERTS), ("Points traités ou sans objet", lambda a: a.statut not in OUVERTS)):
        lignes = [a for a in c.registre if filtre(a)]
        if not lignes:
            continue
        h.append(f"<h2>{groupe} ({len(lignes)})</h2><table><tr><th>Point</th><th>Source</th><th>Action / responsable</th>"
                 "<th>Statut</th><th>Mémoire</th></tr>")
        for a in sorted(lignes, key=lambda a: (a.statut != "Bloquant", a.priorite != "haute")):
            src = escape(f"{a.source or ''}{' p. ' + a.page if a.page else ''}") or "<span class='verif'>à préciser</span>"
            act = escape(" - ".join(x for x in (a.action, a.responsable, a.echeance) if x))
            h.append(f"<tr><td>{escape(a.intitule)}<br/><span class='petit'>{escape(a.categorie)} · {escape(a.destination)}</span></td>"
                     f"<td>{src}</td><td>{act}</td><td>{escape(a.statut)}</td><td>{'oui' if a.memoire else ''}</td></tr>")
        h.append("</table>")
    return _html_vers_pdf("".join(h), Path(chemin))
