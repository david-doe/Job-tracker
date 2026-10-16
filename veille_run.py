"""Veille quotidienne : collecte, tri, enregistrement dans l'onglet « Offres » et mail du matin.

Lancement : `python veille_run.py` (tâche planifiée GitHub Actions ou à la main).
Variables d'environnement :
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD   lecture des alertes et envoi du mail
  GCP_SERVICE_ACCOUNT                 accès au Google Sheet (JSON du compte de service)
  FT_CLIENT_ID, FT_CLIENT_SECRET      API France Travail (facultatif)
  ADZUNA_APP_ID, ADZUNA_APP_KEY       API Adzuna (facultatif)
  GEMINI_API_KEY                      relecture des meilleures offres par l'IA (facultatif)
  VEILLE_MAIL_A                       destinataire du mail (par défaut GMAIL_ADDRESS)

Le dépôt est public, donc les journaux d'exécution aussi : on n'y écrit que des compteurs.
"""

from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date

from veille import NB_MAIL, SCORE_MAIL, Offre, choisir_mots_cles, choisir_type, cles_deja_postulees, dedoublonner, \
    evaluer

ENTETES = ["Cle", "Detectee_le", "Publiee_le", "Score", "Chances", "Titre", "Entreprise", "Lieu", "Contrat",
           "Source", "Lien", "Points_forts", "Points_faibles", "Outils_manquants", "Type_CV", "Mots_cles",
           "Avis_IA", "Statut"]
STATUTS = ["Nouvelle", "Vue", "Postulé", "Écartée"]


# ---------------------------------------------------------------------------
# Onglet « Offres »
# ---------------------------------------------------------------------------
class OffresSheet:
    def __init__(self, classeur):
        try:
            self.ws = classeur.worksheet("Offres")
        except Exception:
            self.ws = classeur.add_worksheet("Offres", rows=2000, cols=len(ENTETES))
            self.ws.append_row(ENTETES)

    def lignes(self) -> list[dict]:
        return self.ws.get_all_records(expected_headers=[])

    def cles(self) -> set[str]:
        return {str(l.get("Cle", "")) for l in self.lignes()}

    def ajouter(self, offres: list[Offre], aujourd_hui: date) -> None:
        if offres:
            self.ws.append_rows([ligne(o, aujourd_hui) for o in offres], value_input_option="RAW")

    def statut(self, cle: str, statut: str) -> None:
        cellule = self.ws.find(cle, in_column=1)
        if cellule:
            self.ws.update_cell(cellule.row, ENTETES.index("Statut") + 1, statut)


class OffresMemoire(OffresSheet):
    """Même interface, en mémoire (tests et mode démo)."""

    def __init__(self, lignes: list[dict] | None = None):
        self._lignes = list(lignes or [])

    def lignes(self):
        return self._lignes

    def ajouter(self, offres, aujourd_hui):
        self._lignes += [dict(zip(ENTETES, ligne(o, aujourd_hui))) for o in offres]

    def statut(self, cle, statut):
        for l in self._lignes:
            if l["Cle"] == cle:
                l["Statut"] = statut


def ligne(o: Offre, aujourd_hui: date) -> list[str]:
    type_cv = choisir_type(o)
    return [o.cle, aujourd_hui.isoformat(), o.date_publication.isoformat() if o.date_publication else "",
            str(o.score), o.chances, o.titre, o.entreprise, o.lieu, o.contrat, o.source, o.url,
            " · ".join(o.raisons_plus), " · ".join(o.raisons_moins), ", ".join(o.outils_manquants),
            type_cv, choisir_mots_cles(o, type_cv), o.avis_ia, "Nouvelle"]


# ---------------------------------------------------------------------------
# Relecture par l'IA (facultative)
# ---------------------------------------------------------------------------
PROFIL_IA = (
    "Étudiant en Master 2 SIAD Data Science (Université de Lille), cherche un stage de 6 mois dès janvier 2027. "
    "Prouvé en projets : SQL (PostgreSQL), Python (pandas, scikit-learn), R, SAS, Power BI (DAX), Excel VBA, "
    "statistiques, régression logistique, ACP, clustering, séries temporelles, prévision, inférence causale, "
    "Streamlit, NLP. Notions seulement : Qlik, Snowflake, Talend. Aucune expérience professionnelle en data "
    "(stage de 2 mois en magasin, job étudiant). Français courant, anglais intermédiaire."
)


def relire_avec_ia(offres: list[Offre], cle_api: str, nombre: int = 15) -> int:
    """Ajuste le score des meilleures offres (±15) avec Gemini. Renvoie le nombre d'offres relues."""
    import requests

    relues = 0
    url = ("https://generativelanguage.googleapis.com/v1beta/models/"
           f"{os.environ.get('GEMINI_MODEL', 'gemini-flash-latest')}:generateContent")
    for o in sorted(offres, key=lambda x: -x.score)[:nombre]:
        if len(o.description) < 150:
            continue
        consigne = (
            f"Profil du candidat : {PROFIL_IA}\n\nOffre : {o.titre} chez {o.entreprise} ({o.lieu}).\n"
            f"{o.description[:6000]}\n\nÉvalue ses chances d'être retenu au premier tri pour cette offre. "
            'Réponds uniquement en JSON : {"ajustement": entier entre -15 et 15, '
            '"avis": "une phrase en français, sans formule"}'
        )
        try:
            r = requests.post(url, params={"key": cle_api}, timeout=40, json={
                "contents": [{"parts": [{"text": consigne}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2}})
            r.raise_for_status()
            texte = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            avis = json.loads(texte)
            ajustement = max(-15, min(15, int(avis.get("ajustement", 0))))
            o.score = max(5, min(95, o.score + ajustement))
            o.avis_ia = str(avis.get("avis", ""))[:300]
            relues += 1
        except Exception:
            continue
    return relues


# ---------------------------------------------------------------------------
# Mail du matin
# ---------------------------------------------------------------------------
def mail_html(offres: list[Offre], stats: dict, aujourd_hui: date) -> tuple[str, str, str]:
    """(objet, texte, html) du récapitulatif."""
    top = offres[:NB_MAIL]
    objet = (f"{len(top)} offre{'s' if len(top) > 1 else ''} de stage à regarder aujourd'hui"
             if top else "Aucune nouvelle offre bien notée aujourd'hui")
    lignes_txt, blocs = [], []
    for o in top:
        age = "date non vérifiée" if o.age_jours is None else ("aujourd'hui" if o.age_jours == 0 else
                                                               f"il y a {o.age_jours} j")
        lignes_txt.append(f"- [{o.score}] {o.titre} — {o.entreprise} ({o.lieu}), {age}\n  {o.url}")
        plus = "<br>".join(html.escape(r) for r in o.raisons_plus[:3])
        moins = "<br>".join(html.escape(r) for r in o.raisons_moins[:2])
        avis = f'<div style="color:#555;margin-top:4px"><i>{html.escape(o.avis_ia)}</i></div>' \
            if o.avis_ia else ""
        type_cv = choisir_type(o)
        couleur = "#1a7f37" if o.score >= 70 else "#9a6700" if o.score >= 50 else "#888"
        blocs.append(f"""
<tr><td style="padding:14px 0;border-bottom:1px solid #eee">
  <div><span style="display:inline-block;min-width:38px;font-weight:700;color:{couleur}">{o.score}</span>
  <a href="{html.escape(o.url)}" style="font-weight:600;color:#7B1E2B;text-decoration:none">{html.escape(o.titre)}</a></div>
  <div style="color:#333">{html.escape(o.entreprise)} · {html.escape(o.lieu)} · {html.escape(o.source)} · {age}</div>
  <div style="color:#1a7f37;font-size:13px;margin-top:4px">{plus}</div>
  <div style="color:#9a3412;font-size:13px">{moins}</div>{avis}
  <div style="font-size:12px;color:#666;margin-top:4px">CV : TYPE = {type_cv} · MOTS_CLES = {html.escape(choisir_mots_cles(o, type_cv))}</div>
</td></tr>""")
    resume = (f"{stats.get('collectees', 0)} offres lues, {stats.get('fraiches', 0)} récentes et pertinentes, "
              f"{stats.get('nouvelles', 0)} nouvelles depuis hier.")
    texte = f"{objet}\n{resume}\n\n" + "\n".join(lignes_txt)
    corps = f"""<div style="font-family:Arial,sans-serif;max-width:680px;color:#222">
<h2 style="color:#7B1E2B;margin-bottom:4px">Veille stage data · {aujourd_hui:%d/%m/%Y}</h2>
<div style="color:#666;margin-bottom:12px">{html.escape(resume)} Score sur 100 : chances estimées de passer le premier tri.</div>
<table style="width:100%;border-collapse:collapse">{''.join(blocs) or '<tr><td>Rien de neuf au-dessus du seuil aujourd&#39;hui.</td></tr>'}</table>
<div style="color:#888;font-size:12px;margin-top:16px">Postule en priorité aux offres de moins de 3 jours. Le lanceur de CV de chaque offre est dans l'app, page « Offres ».</div>
</div>"""
    return objet, texte, corps


def envoyer_mail(adresse: str, mot_de_passe: str, destinataire: str, objet: str, texte: str, corps_html: str):
    import smtplib
    import ssl
    from email.message import EmailMessage

    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = adresse, destinataire, objet
    msg.set_content(texte)
    msg.add_alternative(corps_html, subtype="html")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ssl.create_default_context(), timeout=30) as s:
        s.login(adresse, mot_de_passe.replace(" ", ""))
        s.send_message(msg)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
@dataclass
class Bilan:
    par_source: dict = field(default_factory=dict)
    erreurs: dict = field(default_factory=dict)
    collectees: int = 0
    fraiches: int = 0
    nouvelles: list = field(default_factory=list)
    ecartees: dict = field(default_factory=dict)


def executer(sources, offres_sheet, candidatures=None, aujourd_hui: date | None = None,
             cle_ia: str = "") -> Bilan:
    aujourd_hui = aujourd_hui or date.today()
    bilan = Bilan()
    toutes: list[Offre] = []
    for source in sources:
        nom = getattr(source, "nom", type(source).__name__)
        try:
            lot = source.offres()
            bilan.par_source[nom] = len(lot)
            toutes += lot
        except Exception as e:                      # une source en panne ne bloque pas les autres
            bilan.erreurs[nom] = type(e).__name__
    bilan.collectees = len(toutes)

    deja = cles_deja_postulees(candidatures)
    gardees = []
    for o in dedoublonner(toutes):
        evaluer(o, aujourd_hui, deja)
        if o.ecartee:
            bilan.ecartees[o.ecartee] = bilan.ecartees.get(o.ecartee, 0) + 1
        else:
            gardees.append(o)
    bilan.fraiches = len(gardees)

    connues = offres_sheet.cles()
    nouvelles = [o for o in gardees if o.cle not in connues]
    if cle_ia:
        relire_avec_ia(nouvelles, cle_ia)
    nouvelles.sort(key=lambda o: (-o.score, o.age_jours if o.age_jours is not None else 99))
    offres_sheet.ajouter(nouvelles, aujourd_hui)
    bilan.nouvelles = nouvelles
    return bilan


def sources_depuis_env(env=os.environ) -> list:
    from veille_sources import Adzuna, AlertesGmail, FranceTravail, date_page_hellowork

    sources = []
    if env.get("FT_CLIENT_ID") and env.get("FT_CLIENT_SECRET"):
        sources.append(FranceTravail(env["FT_CLIENT_ID"], env["FT_CLIENT_SECRET"]))
    if env.get("ADZUNA_APP_ID") and env.get("ADZUNA_APP_KEY"):
        sources.append(Adzuna(env["ADZUNA_APP_ID"], env["ADZUNA_APP_KEY"]))
    if env.get("GMAIL_ADDRESS") and env.get("GMAIL_APP_PASSWORD"):
        sources.append(AlertesGmail(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"], date_page_hellowork))
    return sources


def main() -> int:
    env = os.environ
    if not env.get("GCP_SERVICE_ACCOUNT"):
        print("Secret GCP_SERVICE_ACCOUNT absent : veille non configurée, rien à faire.")
        return 0
    sources = sources_depuis_env(env)
    if not sources:
        print("Aucune source configurée (API ou Gmail) : rien à faire.")
        return 0

    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    from store import SheetStore

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(env["GCP_SERVICE_ACCOUNT"]), scope)
        classeur = gspread.authorize(creds).open(env.get("SHEET_NAME") or "Job_Tracker_Data")
        candidatures = SheetStore(classeur.worksheet("Candidatures")).charger()
        bilan = executer(sources, OffresSheet(classeur), candidatures, cle_ia=env.get("GEMINI_API_KEY", ""))
    except Exception as e:                          # message générique : les journaux sont publics
        print(f"Échec de la veille ({type(e).__name__}).")
        return 1

    print("Offres lues par source : " + ", ".join(f"{k} {v}" for k, v in bilan.par_source.items()))
    if bilan.erreurs:
        print("Sources en erreur : " + ", ".join(f"{k} ({v})" for k, v in bilan.erreurs.items()))
    print(f"Collectées {bilan.collectees} · retenues {bilan.fraiches} · nouvelles {len(bilan.nouvelles)}")
    print("Écartées : " + ", ".join(f"{k} {v}" for k, v in sorted(bilan.ecartees.items())))

    a_envoyer = [o for o in bilan.nouvelles if o.score >= SCORE_MAIL and o.date_publication is not None]
    if env.get("GMAIL_ADDRESS") and env.get("GMAIL_APP_PASSWORD") and env.get("VEILLE_MAIL", "1") != "0":
        stats = {"collectees": bilan.collectees, "fraiches": bilan.fraiches, "nouvelles": len(bilan.nouvelles)}
        objet, texte, corps = mail_html(a_envoyer, stats, date.today())
        try:
            envoyer_mail(env["GMAIL_ADDRESS"], env["GMAIL_APP_PASSWORD"],
                         env.get("VEILLE_MAIL_A") or env["GMAIL_ADDRESS"], objet, texte, corps)
            print(f"Mail envoyé ({len(a_envoyer[:NB_MAIL])} offres).")
        except Exception as e:
            print(f"Mail non envoyé ({type(e).__name__}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
