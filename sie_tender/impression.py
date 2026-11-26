"""Documents imprimables (PDF) produits localement : fiche de renseignement, synthèse du registre de vigilance.

Le PDF est construit avec ReportLab (licence BSD) à partir d'un HTML simple généré ici : aucun service externe.
"""
from __future__ import annotations

import re
from datetime import datetime
from html import escape
from pathlib import Path

from .export import LIBELLES, ref
from .models import Consultation, Statut

SECTIONS_FICHE = [
    ("1. Identification de l'appel d'offres", ["intitule", "reference", "objet", "procedure"]),
    ("2. Localisation du projet", ["lieu"]),
    ("3. Dates importantes", ["date_limite_remise", "date_limite_questions", "visite", "visite_obligatoire", "visite_sur_rdv",
                              "fiche_visite_signee", "contacts", "duree_validite_offres"]),
    ("4. Acteurs du projet", ["maitre_ouvrage", "maitre_oeuvre", "architecte", "coordonnateur_sps", "coordinateur_ssi", "intervenants"]),
]


_SPANS = {"verif": ('<font color="#b45309"><b>', "</b></font>"), "petit": ('<font size="7.5" color="#5b6b7b">', "</font>")}


def _en_ligne(html: str) -> str:
    """Balises en ligne de notre HTML -> balisage ReportLab (<b>, <br/>, <font>)."""
    def span(m):
        debut, fin = _SPANS.get(m.group(1), ("", ""))
        return f"{debut}{m.group(2)}{fin}"
    return re.sub(r"<span class=['\"](\w+)['\"]>(.*?)</span>", span, html, flags=re.S).strip()


def _html_vers_pdf(html: str, chemin: Path) -> Path:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    corps = ParagraphStyle("corps", fontName="Helvetica", fontSize=9, leading=11.5, textColor="#1b2733", spaceAfter=3)
    styles = {"h1": ParagraphStyle("h1", parent=corps, fontName="Helvetica-Bold", fontSize=15, leading=18, textColor="#12385c"),
              "h2": ParagraphStyle("h2", parent=corps, fontName="Helvetica-Bold", fontSize=11, leading=14, textColor="#12385c",
                                   spaceBefore=8),
              "petit": ParagraphStyle("petit", parent=corps, fontSize=7.5, leading=9.5, textColor="#5b6b7b"),
              "cellule": ParagraphStyle("cellule", parent=corps, spaceAfter=0)}
    elements = []
    for m in re.finditer(r"<(h1|h2|p|table|ul)([^>]*)>(.*?)</\1>", html, flags=re.S):
        balise, attrs, contenu = m.groups()
        if balise == "table":
            lignes, fusions = [], []
            for i, tr in enumerate(re.findall(r"<tr>(.*?)</tr>", contenu, flags=re.S)):
                ligne = []
                for th, att, txt in re.findall(r"<(t[hd])([^>]*)>(.*?)</t[hd]>", tr, flags=re.S):
                    texte = f"<b>{_en_ligne(txt)}</b>" if th == "th" else _en_ligne(txt)
                    if "verif" in att:
                        texte = f'<font color="#b45309"><b>{texte}</b></font>'
                    ligne.append(Paragraph(texte, styles["cellule"]))
                    n = re.search(r"colspan=['\"]?(\d+)", att)
                    if n:
                        fusions.append((len(ligne) - 1, i, len(ligne) - 2 + int(n.group(1)), i))
                        ligne += [""] * (int(n.group(1)) - 1)
                lignes.append(ligne)
            if not lignes:
                continue
            largeur = max(len(l) for l in lignes)
            lignes = [l + [""] * (largeur - len(l)) for l in lignes]
            t = Table(lignes, repeatRows=1, hAlign="LEFT", colWidths=[(A4[0] - 72) / largeur] * largeur)
            st = [("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#b8c4d0")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                  ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef4"))]
            st += [("SPAN", (a, b), (c, d)) for a, b, c, d in fusions]
            t.setStyle(TableStyle(st))
            elements += [t, Spacer(1, 4)]
        elif balise == "ul":
            items = [ListItem(Paragraph(_en_ligne(li), corps)) for li in re.findall(r"<li>(.*?)</li>", contenu, flags=re.S)]
            elements.append(ListFlowable(items, bulletType="bullet", leftIndent=12))
        else:
            style = styles["petit"] if "petit" in attrs else styles.get(balise, corps)
            elements.append(Paragraph(_en_ligne(contenu), style))
    chemin = Path(chemin)
    SimpleDocTemplate(str(chemin), pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=40).build(elements)
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
