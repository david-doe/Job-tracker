import base64
import email
import sys
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from veille import Offre, choisir_type, dedoublonner, date_relative, evaluer, lanceur, titre_cv
from veille_run import OffresMemoire, executer, mail_html
from veille_sources import lire_alerte, lire_indeed, lire_linkedin, url_cachee

AUJ = date(2026, 10, 16)

LINKEDIN = """Votre alerte Emploi pour Analyste de données (Corbas)
De nouvelles offres d’emploi correspondent à vos préférences.

Data Analyst Senior – Power BI H/F
STEP UP
Saint-Priest

1 ancien collègue
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4479560469/?trackingId=x

---------------------------------------------------------

Stage - Data Analyst Power BI H/F
ICSIS
Lille

2 anciens élèves
Voir l’offre d’emploi : https://www.linkedin.com/comm/jobs/view/4479529340/?trackingId=y

---------------------------------------------------------

Voir toutes les offres d’emploi https://www.linkedin.com/jobs/search-results/?keywords=Data&f_TPR=a1791911734-&geoId=1
"""

INDEED = """Indeed Job Alert
1 nouvel emploi Data - Nord-Pas de Calais

Jobs 1-1 of 1 new job
See matching results on Indeed: https://fr.indeed.com/jobs?q=Data&l=Nord

Stage de fin d'études - Consultant Data & IA H/F - Régions
AVISIA - Lille (59)
1 300 € par mois
Candidature simplifiée
Pour plus d'information sur ces initiatives data, consultez notre page.
Publié à l'instant
https://fr.indeed.com/rc/clk/dl?jk=95533b17278c0d87&from=ja

Stage Data Analyst H/F
Vieille Boîte - Paris (75)
Publié il y a plus de 30 jours
https://fr.indeed.com/rc/clk/dl?jk=aaaabbbbccccdddd&from=ja

Ne partagez pas cet email
"""


def lien_hellowork(identifiant):
    charge = ("doe@gmail.com\U0001faa2https://www.hellowork.com/fr-fr/emplois/"
              f"{identifiant}.html?utm_source=jobalert").encode()
    return "https://emails.hellowork.com/clic/abc/3/def/" + base64.urlsafe_b64encode(charge).decode().rstrip("=")


HELLOWORK = f"""<html><head><style>.x{{}}</style></head><body><table>
<tr><td><a href="{lien_hellowork(84440609)}">Data Analyst Direction des Investissements - Stage H/F</a></td></tr>
<tr><td>AXA France</td></tr><tr><td>Nanterre - 92</td></tr><tr><td>Stage</td></tr>
<tr><td><a href="{lien_hellowork(84440609)}">Voir l’offre</a></td></tr>
<tr><td><a href="{lien_hellowork(83837245)}">Stage - Data Analyst en Qualité Industrielle H/F</a></td></tr>
<tr><td>Safran - CDD/Stage/Alternance</td></tr><tr><td>Colombes - 92</td></tr><tr><td>Stage</td></tr>
</table></body></html>"""


def message(expediteur, texte="", page="", recu="Thu, 15 Oct 2026 12:24:03 +0000"):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = expediteur, "doe@gmail.com", "Alerte", recu
    if texte:
        msg.set_content(texte)
    if page:
        if texte:
            msg.add_alternative(page, subtype="html")
        else:
            msg.set_content(page, subtype="html")
    return email.message_from_bytes(msg.as_bytes())


# --- Lecture des alertes ----------------------------------------------------
def test_linkedin_blocs_et_date_de_fenetre():
    offres = lire_linkedin(LINKEDIN, date(2026, 10, 15))
    assert [o.titre for o in offres] == ["Data Analyst Senior – Power BI H/F", "Stage - Data Analyst Power BI H/F"]
    assert offres[1].entreprise == "ICSIS" and offres[1].lieu == "Lille"
    assert offres[1].url == "https://www.linkedin.com/jobs/view/4479529340/"
    assert offres[0].date_publication == date(2026, 10, 13)   # horodatage f_TPR


def test_indeed_age_lu_dans_l_alerte():
    offres = lire_indeed(INDEED, date(2026, 10, 15))
    assert len(offres) == 2
    assert offres[0].entreprise == "AVISIA" and offres[0].lieu == "Lille (59)"
    assert offres[0].date_publication == date(2026, 10, 15)
    assert offres[1].date_publication < date(2026, 9, 15)       # « plus de 30 jours »


def test_hellowork_lien_decode_et_date_de_la_page():
    dates = {"https://www.hellowork.com/fr-fr/emplois/84440609.html": date(2026, 10, 14)}
    offres = lire_alerte(message("Hellowork <alerte@emails.hellowork.com>", page=HELLOWORK),
                         verifier_page=dates.get)
    assert [o.id_source for o in offres] == ["84440609", "83837245"]
    assert offres[0].entreprise == "AXA France" and offres[0].lieu == "Nanterre - 92" and offres[0].contrat == "Stage"
    assert offres[0].date_publication == date(2026, 10, 14)
    assert offres[1].date_publication is None                     # page non vérifiée


def test_site_carriere():
    page = ('<p><a href="http://careers.opmobility.com/job/Lille-Stage-Data-Analyst/1402424433/?from=email">'
            'Stage - Data Analyst - Lille, FR</a></p>')
    offres = lire_alerte(message("OPmobility-jobnotification@noreply12.jobs2web.com", page=page))
    assert len(offres) == 1 and offres[0].entreprise == "OPmobility" and offres[0].lieu == "Lille, FR"
    assert offres[0].date_publication == date(2026, 10, 15)


def test_expediteur_inconnu_ignore():
    assert lire_alerte(message("stage.fr@mysmartjobboard.com", texte="Stage Data Analyst Emotors")) == []


def test_url_cachee():
    assert url_cachee(lien_hellowork(1)).startswith("https://www.hellowork.com/fr-fr/emplois/1.html")
    assert url_cachee("https://x.com/r?url=https%3A%2F%2Fa.fr%2Fjob%2F1") == "https://a.fr/job/1"


def test_dates_relatives():
    assert date_relative("Publié il y a 3 jours", AUJ) == AUJ - timedelta(days=3)
    assert date_relative("Publiée le 14/10/2026", AUJ) == date(2026, 10, 14)
    assert date_relative("Posted 2 weeks ago", AUJ) == AUJ - timedelta(days=14)
    assert date_relative("Hier", AUJ) == AUJ - timedelta(days=1)


# --- Score ------------------------------------------------------------------
def offre(**k):
    base = dict(source="Adzuna", titre="Stage Data Analyst H/F", entreprise="PME Data", lieu="Lille (59)",
                date_publication=AUJ, description="Stage de 6 mois dès janvier 2027. SQL, Python, Power BI, "
                                                   "tableaux de bord et statistiques.")
    base.update(k)
    return evaluer(Offre(**base), AUJ)


def test_offre_ideale_tres_bien_notee():
    o = offre()
    assert not o.ecartee and o.score >= 85 and o.chances == "Forte"


def test_exclusions():
    assert offre(titre="Data Analyst Alternance H/F", contrat="Alternance").ecartee == "Pas un stage"
    assert offre(titre="Stage comptable", description="Stage comptabilité 6 mois").ecartee == "Pas un poste data"
    assert offre(date_publication=AUJ - timedelta(days=12)).ecartee.startswith("Publiée il y a plus")
    assert offre(titre="Stage Data Engineer Spark").ecartee == "Métier éloigné du profil"
    assert offre(titre="Stage CDI Data Analyst").ecartee == ""     # « stage » l'emporte


def test_penalites():
    ideal = offre(lieu="Lyon", description="Stage. SQL et Python.").score
    assert 55 <= ideal <= 85
    assert offre(entreprise="Safran Electronics", lieu="Lyon", description="Stage. SQL et Python.").score < ideal
    assert offre(description="Stage 6 mois. Databricks, Spark, Azure obligatoires.").outils_manquants == \
        ["Databricks", "Spark", "Azure"]
    assert offre(lieu="Lyon", description="Stage bilingue chinois. SQL et Python.").score <= ideal - 25
    assert offre(lieu="Lyon", description="Stage. SQL et Python.", date_publication=None).score < ideal
    assert offre(lieu="Lyon", description="Stage à pourvoir en novembre 2026 pour 4 mois. SQL et Python.").score < ideal - 10


def test_deja_postule_et_doublons():
    deja = {Offre(source="", titre="Stage Data Analyst Power BI & DAX", entreprise="Emotors").cle}
    o = evaluer(Offre(source="Indeed", titre="Stage - Data Analyst Power BI & DAX H/F", entreprise="Emotors",
                      date_publication=AUJ), AUJ, deja)
    assert o.ecartee == "Déjà postulé"
    a = Offre(source="LinkedIn", titre="Stage Data Analyst H/F", entreprise="ICSIS", date_publication=None)
    b = Offre(source="Adzuna", titre="Stage - Data Analyst", entreprise="ICSIS", date_publication=AUJ,
              description="long" * 50)
    seule = dedoublonner([a, b])
    assert len(seule) == 1 and seule[0].source == "Adzuna"


def test_lanceur():
    o = Offre(source="x", titre="STAGE – Data Analyst Power BI & DAX H/F", entreprise="Emotors",
              description="Tableaux de bord Power BI sur la non-qualité fournisseurs, mesures DAX, SQL, production.")
    assert titre_cv(o.titre) == "Stage Data Analyst Power BI & DAX"
    assert choisir_type(o) in {"pilotage", "industrie"}
    nom, contenu = lanceur(o)
    assert nom == "cv_offre_Emotors.py"
    compile(contenu, nom, "exec")
    assert "TITRE = 'Stage Data Analyst Power BI & DAX'" in contenu and "Power BI" in contenu


# --- Pipeline ---------------------------------------------------------------
class Source:
    def __init__(self, nom, offres=None, erreur=False):
        self.nom, self._offres, self.erreur = nom, offres or [], erreur

    def offres(self):
        if self.erreur:
            raise ConnectionError
        return self._offres


def test_pipeline_complet():
    o1 = Offre(source="Adzuna", titre="Stage Data Analyst", entreprise="PME", lieu="Lille", date_publication=AUJ,
               description="6 mois janvier 2027 SQL Python Power BI")
    o2 = Offre(source="Indeed", titre="Data Analyst CDI", entreprise="B", contrat="CDI", date_publication=AUJ)
    o3 = Offre(source="Indeed", titre="Stage Data Scientist", entreprise="C", lieu="Paris", date_publication=AUJ)
    feuille = OffresMemoire([{"Cle": o3.cle, "Statut": "Vue"}])
    candidatures = pd.DataFrame([{"Entreprise": "Z", "Poste": "Autre"}])
    bilan = executer([Source("A", [o1, o2, o3]), Source("B", erreur=True)], feuille, candidatures, AUJ)
    assert bilan.erreurs == {"B": "ConnectionError"} and bilan.collectees == 3
    assert [o.titre for o in bilan.nouvelles] == ["Stage Data Analyst"]   # o3 déjà connue, o2 écartée
    assert bilan.ecartees == {"Pas un stage": 1}
    assert len(feuille.lignes()) == 2 and feuille.lignes()[1]["Statut"] == "Nouvelle"
    objet, texte, corps = mail_html(bilan.nouvelles, {"collectees": 3}, AUJ)
    assert "1 offre" in objet and "Stage Data Analyst" in corps and "TYPE =" in corps


class Reponse:
    def __init__(self, donnees, code=200):
        self._d, self.status_code = donnees, code

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    def __init__(self, reponses):
        self.reponses, self.appels = reponses, []

    def post(self, url, **k):
        return Reponse({"access_token": "t"})

    def get(self, url, **k):
        self.appels.append((url, k.get("params")))
        return self.reponses(url, k.get("params") or {})


def test_api_france_travail_et_adzuna():
    from veille_sources import Adzuna, FranceTravail

    ft = FranceTravail("id", "secret", Session(lambda u, p: Reponse({"resultats": [{
        "id": "201ABC", "intitule": "Stage Data Analyst (H/F)", "dateCreation": "2026-10-15T09:00:00.000Z",
        "lieuTravail": {"libelle": "59 - Lille"}, "entreprise": {"nom": "ACME"}, "typeContratLibelle": "Stage",
        "description": "SQL Python"}]})))
    o = ft.offres()[0]
    assert (o.titre, o.entreprise, o.lieu, o.date_publication) == \
        ("Stage Data Analyst (H/F)", "ACME", "59 - Lille", date(2026, 10, 15))
    assert o.url.endswith("/detail/201ABC")

    az = Adzuna("id", "key", Session(lambda u, p: Reponse({"results": [{
        "id": 1, "title": "<strong>Stage</strong> Data", "company": {"display_name": "B"},
        "location": {"display_name": "Paris"}, "redirect_url": "https://adzuna/1",
        "created": "2026-10-14T08:00:00Z", "description": "<b>SQL</b>"}]})))
    o = az.offres()[0]
    assert (o.titre, o.description, o.date_publication) == ("Stage Data", "SQL", date(2026, 10, 14))


def test_main_sans_configuration(monkeypatch, capsys):
    import veille_run

    for v in ("GCP_SERVICE_ACCOUNT", "GMAIL_ADDRESS", "FT_CLIENT_ID", "ADZUNA_APP_ID"):
        monkeypatch.delenv(v, raising=False)
    assert veille_run.main() == 0
    assert "rien à faire" in capsys.readouterr().out


def test_compte_service_formats():
    import base64
    import json as _json
    from veille_run import lire_compte_service
    d = {"type": "service_account", "private_key": "-----BEGIN-----\nabc\n-----END-----\n", "client_email": "x@y"}
    j = _json.dumps(d)
    assert lire_compte_service(j) == d
    assert lire_compte_service(base64.b64encode(j.encode()).decode()) == d
    toml = '[gcp_service_account]\ntype = "service_account"\nprivate_key = "-----BEGIN-----\\nabc\\n-----END-----\\n"\nclient_email = "x@y"\n'
    assert lire_compte_service(toml) == d
    brut = '{"type": "service_account", "private_key": "-----BEGIN-----\nabc\n-----END-----\n", "client_email": "x@y"}'
    assert lire_compte_service(brut) == d
    import pytest
    with pytest.raises(ValueError):
        lire_compte_service("n'importe quoi")


def test_identifiants_gmail_robustes():
    from veille_run import identifiants_gmail
    ok = ("moi@gmail.com", "abcdefghijklmnop")
    assert identifiants_gmail({"GMAIL_ADDRESS": "moi@gmail.com", "GMAIL_APP_PASSWORD": "abcd efgh ijkl mnop"}) == ok
    assert identifiants_gmail({"GMAIL_ADDRESS": "abcd efgh ijkl mnop", "GMAIL_APP_PASSWORD": "moi@gmail.com"}) == ok
    assert identifiants_gmail({"GMAIL_ADDRESS": "David <moi@gmail.com>\n", "GMAIL_APP_PASSWORD": " abcdefghijklmnop "}) == ok


def test_postule_approche_et_type():
    import pandas as pd
    from veille import Offre, choisir_type, cles_deja_postulees, evaluer
    from datetime import date
    cand = pd.DataFrame([{"Date": "2026-10-06", "Entreprise": "Heineken", "Poste": "Data Analyst"}])
    o = Offre(source="Adzuna", titre="Stage - Data analyst H/F - Rueil Malmaison (92)", entreprise="Heineken",
              lieu="Rueil", url="u", date_publication=date(2026, 10, 14), description="Stage de 6 mois en data.")
    evaluer(o, date(2026, 10, 16), cles_deja_postulees(cand))
    assert not o.ecartee and any("déjà postulé" in r for r in o.raisons_moins)
    t = Offre(source="Adzuna", titre="Stage de Fin d'Études - Consultant Data App & Automation H/F",
              entreprise="Talan", lieu="Paris", url="u", description="Rejoins notre cabinet de conseil.")
    assert choisir_type(t) == "conseil"
    g = Offre(source="Adzuna", titre="Stage de fin d'études - Data H/F", entreprise="X", lieu="Paris", url="u")
    assert choisir_type(g) == "generique"
