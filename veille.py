"""Veille des offres de stage : fraîcheur, dédoublonnage, score de chances et lanceur du CV.

Ce module ne fait aucun appel réseau : les sources sont dans veille_sources.py.
Tout ce qui décrit le profil (compétences prouvées, domaines du CV, lieux visés) est regroupé
dans PROFIL pour être ajusté au même endroit.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------
JOURS_MAX = 7            # une offre plus ancienne n'est jamais proposée
SCORE_MAIL = 50          # score minimum pour figurer dans le mail du matin
NB_MAIL = 10             # nombre d'offres dans le mail du matin

# Compétences prouvées par le CV : (libellé, motif cherché dans le texte normalisé)
COMPETENCES = [
    ("SQL", r"\bsql\b"), ("Python", r"\bpython\b"), ("Power BI", r"power ?bi"), ("Excel", r"\bexcel\b"),
    ("VBA", r"\bvba\b"), ("PostgreSQL", r"postgre"), ("SAS", r"\bsas\b"), ("DAX", r"\bdax\b"),
    ("R", None),  # lettre seule : cherchée à part dans le texte brut
    ("Machine learning", r"machine learning|apprentissage automatique"), ("Statistiques", r"statisti"),
    ("Prévision", r"prevision|forecast"), ("Séries temporelles", r"series? temporelles?|time series"),
    ("Régression", r"regression"), ("Segmentation", r"segmentation|clustering"), ("Scoring", r"\bscoring\b"),
    ("Tableaux de bord", r"tableaux? de bord|dashboard"), ("KPI", r"\bkpis?\b"), ("Reporting", r"reporting"),
    ("ETL", r"\betl\b"), ("Qualité des données", r"qualite des donnees|data quality"),
    ("Churn", r"churn|attrition"), ("Streamlit", r"streamlit"), ("Git", r"\bgit(hub|lab)?\b"),
    ("Inférence causale", r"causal|doubles? differences|difference[- ]in[- ]differences"),
    ("NLP", r"\bnlp\b|traitement (automatique )?du langage"), ("IA générative", r"ia generative|genai|\bllms?\b"),
]
# Vus en séminaire seulement : ni bonus ni pénalité
NOTIONS = [r"\bqlik", r"snowflake", r"talend"]
# Outils souvent exigés que le CV ne prouve pas
OUTILS_ABSENTS = [
    ("Databricks", r"databricks"), ("Spark", r"\b(py)?spark\b"), ("Tableau", r"\btableau (software|desktop|server)\b|\btableau\b(?! de bord)"),
    ("SAP", r"\bsap\b"), ("Azure", r"\bazure\b"), ("AWS", r"\baws\b"), ("Scala", r"\bscala\b"), ("Java", r"\bjava\b"),
    ("Looker", r"looker"), ("dbt", r"\bdbt\b"), ("Airflow", r"airflow"), ("Kafka", r"kafka"), ("Docker", r"docker"),
    ("Hadoop", r"hadoop"), ("Salesforce", r"salesforce"), ("Alteryx", r"alteryx"), ("Dataiku", r"dataiku"),
]

TITRE_DATA = re.compile(
    r"data|donnee|analyst|analyste|\bbi\b|business intelligence|power ?bi|reporting|statisti|machine learning|"
    r"\bia\b|\bai\b|intelligence artificielle|charge d.?etudes|econometr|quantitati|pilotage|controle de gestion|"
    r"performance|insights?|dataviz|visualisation")
TITRE_BON = re.compile(
    r"data analyst|analyste (de )?donnees|analyste data|data scien|business intelligence|\bbi\b|power ?bi|"
    r"reporting|charge d.?etudes|statisti|business analyst|analyste|data & ?ia|data et ia|consultant data|"
    r"controle de gestion|pilotage|performance|insights?")
TITRE_LOIN = re.compile(
    r"data engineer|ingenieur (de )?donnees|ingenieur data|developpeu|developer|devops|mlops|fullstack|"
    r"full stack|backend|front.?end|cyber|reseau|systeme|architecte|testeu|commercial|vendeu|comptable|"
    r"juriste|marketing digital|community|graphis|lidar|electri|mecani|chimiste|biolog")
NON_STAGE = re.compile(r"\b(alternance|apprenti|apprentissage|cdi|cdd|freelance|interim|v\.?i\.?e\b)")
STAGE = re.compile(r"\b(stage|stagiaire|internship|intern|fin d.?etudes|pfe)\b")
DEBUT_BON = re.compile(r"janvier 2027|fevrier 2027|debut 2027|janv\.? 2027|january 2027|fin d.?etudes|6 mois|six mois")
DEBUT_MAUVAIS = re.compile(r"(septembre|octobre|novembre|decembre) 2026|des que possible|asap|immediat")
DUREE_COURTE = re.compile(r"\b(2|3|4) mois\b")
LANGUE_RARE = re.compile(r"bilingue (chinois|allemand|japonais|italien|neerlandais|espagnol|portugais|coreen|arabe)|"
                         r"(chinois|allemand|japonais|neerlandais|coreen) (courant|bilingue|obligatoire)")
ANGLAIS_EXIGE = re.compile(r"anglais (courant|bilingue|fluent|c1)|fluent english|english (is )?mandatory|bilingue anglais")
NIVEAU_HORS = re.compile(r"doctorat|phd|these\b|bac ?\+ ?[23]\b")

LIEUX_PROCHES = re.compile(r"lille|roubaix|tourcoing|villeneuve.d.?ascq|marcq|lens\b|douai|arras|valenciennes|"
                           r"dunkerque|calais|boulogne.sur.mer|amiens|\(59\)|\(62\)|- 59\b|- 62\b|hauts.de.france|nord\b")
LIEUX_PARIS = re.compile(r"paris|ile.de.france|la defense|nanterre|puteaux|courbevoie|boulogne.billancourt|"
                         r"issy|levallois|saint.denis|montreuil|massy|velizy|\(75\)|\(92\)|\(93\)|\(94\)|\(78\)|\(91\)|"
                         r"- 75\b|- 92\b|- 93\b|- 94\b|- 78\b|- 91\b")
TELETRAVAIL = re.compile(r"teletravail|remote|a distance")

# Très grands groupes : beaucoup plus de candidats par offre, donc moins de chances au premier tri
GRANDS_GROUPES = re.compile(
    r"\b(safran|thales|airbus|dassault|groupe adp|axa|bnp|societe generale|credit agricole sa|l.?oreal|lvmh|"
    r"totalenergies|orange|edf|engie|sncf|ratp|renault|stellantis|capgemini|accenture|deloitte|kpmg|pwc|"
    r"ernst|ey\b|danone|sanofi|michelin|decathlon|carrefour|auchan|leroy merlin|google|amazon|microsoft|"
    r"meta|apple|ibm|hermes|chanel|kering|airfrance|air france|bouygues|vinci|saint.gobain|schneider)\b")

# Domaines du CV (TYPE du lanceur) : mots repérés dans l'offre, dans l'ordre de priorité du moteur
DOMAINES = {
    "supply_chain": ["supply", "logisti", "approvision", "stock", "flux", "entrepot", "achat", "inventaire",
                     "s&op", "planification", "fournisseur"],
    "retail": ["retail", "magasin", "grande distribution", "e-commerce", "ecommerce", "enseigne", "assortiment",
               "point de vente", "merchandising", "promotion"],
    "commercial": ["commercial", "sales", "force de vente", "ventes", "chiffre d'affaires", "objectifs",
                   "business developer", "pricing", "performance commerciale"],
    "client": ["client", "crm", "marketing", "fidelis", "churn", "attrition", "segmentation", "campagne",
               "parcours", "satisfaction"],
    "pilotage": ["reporting", "tableau de bord", "tableaux de bord", "dashboard", "kpi", "pilotage",
                 "controle de gestion", "business intelligence", "indicateurs"],
    "data_science": ["machine learning", "modele", "modelisation", "predict", "scikit", "causal", "deep learning",
                     "nlp", "algorithme", "intelligence artificielle", "data scien"],
    "qualite_donnees": ["qualite des donnees", "data quality", "gouvernance", "referentiel", "mdm", "fiabilis",
                        "data management", "catalogue", "anomalies", "nettoyage"],
    "service_public": ["service public", "collectivite", "energie", "mobilite", "frequentation", "usagers",
                       "ferroviaire", "metropole", "territoire", "voyageurs", "politique publique"],
    "etudes_economiques": ["etudes eco", "etudes stat", "charge d'etudes", "charge etudes", "econom", "conjoncture", "enquete", "sondage", "barometre", "panel",
                           "statistique publique", "insee", "banque centrale", "prix", "inflation"],
    "banque_finance": ["banque", "bancaire", "assurance", "credit", "risque", "financ", "fraude", "actuariat",
                       "mutuelle", "solvabilite", "bale", "epargne"],
    "industrie": ["industri", "production", "usine", "manufactur", "maintenance", "aeronautique", "automobile",
                  "chimie", "non-qualite", "lean", "atelier", "fabrication", "defense", "naval", "procede"],
    "rh": ["ressources humaines", " rh ", "sirh", "people analytics", "talent", "recrutement", "paie",
           "effectifs", "turnover", "absenteisme"],
    "conseil": ["conseil", "consultant", "cabinet", "esn", "missions chez nos clients",
                "accompagnement de nos clients", "transformation data"],
}
MOTS_CLES_DEFAUT = {
    "generique": ["SQL", "Python", "Power BI", "Statistiques", "Prévision"],
    "supply_chain": ["SQL", "Power BI", "Excel (VBA)", "Prévision", "Suivi des stocks"],
    "retail": ["SQL", "Power BI", "Python", "Prévision", "KPI"],
    "commercial": ["SQL", "Power BI", "Python", "Segmentation", "Scoring"],
    "client": ["SQL", "Python", "Segmentation", "Churn", "Power BI"],
    "pilotage": ["Power BI", "SQL", "Excel (VBA)", "Reporting", "KPI"],
    "data_science": ["Python", "Machine learning", "SQL", "Statistiques", "Power BI"],
    "qualite_donnees": ["SQL", "Qualité des données", "ETL", "PostgreSQL", "Power BI"],
    "service_public": ["SQL", "Python", "Prévision", "Inférence causale", "Power BI"],
    "etudes_economiques": ["Statistiques", "Inférence causale", "R", "Python", "Séries temporelles"],
    "banque_finance": ["SQL", "Python", "Scoring", "Statistiques", "Power BI"],
    "industrie": ["SQL", "Power BI", "Python", "Qualité des données", "KPI"],
    "rh": ["Python", "SQL", "Power BI", "Segmentation", "Statistiques"],
    "conseil": ["SQL", "Python", "Power BI", "Machine learning", "Tableaux de bord"],
}


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------
def norm(texte: str) -> str:
    texte = unicodedata.normalize("NFD", (texte or "").lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texte.replace("’", "'")).strip()


@dataclass
class Offre:
    source: str
    titre: str
    entreprise: str = ""
    lieu: str = ""
    url: str = ""
    date_publication: date | None = None   # None = date non vérifiée
    contrat: str = ""                       # tel qu'annoncé par la source
    description: str = ""
    id_source: str = ""
    # remplis par evaluer()
    score: int = 0
    raisons_plus: list[str] = field(default_factory=list)
    raisons_moins: list[str] = field(default_factory=list)
    outils_manquants: list[str] = field(default_factory=list)
    ecartee: str = ""                       # motif si l'offre ne doit pas être proposée
    avis_ia: str = ""

    @property
    def cle(self) -> str:
        """Clé de dédoublonnage entre sources : entreprise + titre simplifiés."""
        titre = re.sub(r"\b(h/f|f/h|h/f/x|f/h/x|m/f|stage|stagiaire|internship|-|–)\b", " ", norm(self.titre))
        titre = re.sub(r"[^a-z0-9 ]", " ", titre)
        entreprise = re.sub(r"\b(groupe|group|sa|sas|france|-)\b", " ", norm(self.entreprise))
        base = " ".join(re.sub(r"[^a-z0-9 ]", " ", entreprise).split()) + "|" + " ".join(titre.split())
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    @property
    def age_jours(self) -> int | None:
        return None if self.date_publication is None else (date.today() - self.date_publication).days

    @property
    def chances(self) -> str:
        return "Forte" if self.score >= 70 else "Moyenne" if self.score >= 50 else "Faible"


# ---------------------------------------------------------------------------
# Score
# ---------------------------------------------------------------------------
def _competences(texte_n: str, texte_brut: str) -> list[str]:
    trouvees = []
    for libelle, motif in COMPETENCES:
        if motif is None:
            if re.search(r"(?<![\w&'’])R(?![\w&'’+#])", texte_brut):
                trouvees.append(libelle)
        elif re.search(motif, texte_n):
            trouvees.append(libelle)
    return trouvees


def evaluer(offre: Offre, aujourd_hui: date | None = None, deja_postule: set[str] | None = None) -> Offre:
    """Calcule le score (5 à 95) et les raisons. Renseigne `ecartee` si l'offre ne doit pas être proposée."""
    aujourd_hui = aujourd_hui or date.today()
    titre_n = norm(offre.titre)
    texte_brut = f"{offre.titre}\n{offre.description}"
    texte_n = norm(f"{offre.titre} {offre.contrat} {offre.lieu} {offre.description}")
    plus, moins = offre.raisons_plus, offre.raisons_moins
    plus.clear(), moins.clear()
    score = 50

    # --- Exclusions --------------------------------------------------------
    contrat_n = norm(offre.contrat)
    est_stage = bool(STAGE.search(titre_n) or STAGE.search(contrat_n))
    if NON_STAGE.search(contrat_n) and not est_stage:
        offre.ecartee = "Pas un stage"
    elif NON_STAGE.search(titre_n) and not STAGE.search(titre_n):
        offre.ecartee = "Pas un stage"
    elif not est_stage and not STAGE.search(texte_n):
        offre.ecartee = "Pas un stage"
    elif not TITRE_DATA.search(titre_n):
        offre.ecartee = "Pas un poste data"
    elif TITRE_LOIN.search(titre_n) and not TITRE_BON.search(titre_n):
        offre.ecartee = "Métier éloigné du profil"
    elif offre.date_publication and (aujourd_hui - offre.date_publication).days > JOURS_MAX:
        offre.ecartee = f"Publiée il y a plus de {JOURS_MAX} jours"
    elif NIVEAU_HORS.search(texte_n):
        offre.ecartee = "Niveau d'études différent"
    elif deja_postule and offre.cle in deja_postule:
        offre.ecartee = "Déjà postulé"

    proche = deja_postule.proche(offre) if isinstance(deja_postule, Postulees) and not offre.ecartee else None
    if proche:
        score -= 20
        moins.append(f"Tu as peut-être déjà postulé : {proche}")
    if re.search(r"talents? handicap|hanploi|agefiph", norm(f"{offre.entreprise} {offre.titre}")):
        score -= 15
        moins.append("Publiée via un forum de recrutement dédié aux candidats en situation de handicap")

    # --- Métier ------------------------------------------------------------
    if TITRE_BON.search(titre_n):
        score += 12
        plus.append("Intitulé proche de ton profil")
    if TITRE_LOIN.search(titre_n):
        score -= 15
        moins.append("Intitulé plutôt orienté ingénierie")

    # --- Compétences -------------------------------------------------------
    trouvees = _competences(texte_n, texte_brut)
    if trouvees:
        score += min(15, 3 * len(trouvees))
        plus.append("Compétences demandées que tu prouves : " + ", ".join(trouvees[:5]))
    absents = [nom for nom, motif in OUTILS_ABSENTS if re.search(motif, texte_n)]
    offre.outils_manquants = absents
    if absents:
        score -= min(12, 4 * len(absents))
        moins.append("Outils demandés absents du CV : " + ", ".join(absents[:4]))

    # --- Fraîcheur ---------------------------------------------------------
    age = None if offre.date_publication is None else (aujourd_hui - offre.date_publication).days
    if age is None:
        score -= 10
        moins.append("Date de publication non vérifiée")
    elif age <= 1:
        score += 12
        plus.append("Publiée il y a moins de 2 jours : peu de candidats")
    elif age <= 3:
        score += 6
        plus.append(f"Publiée il y a {age} jours")
    else:
        moins.append(f"Publiée il y a {age} jours")

    # --- Lieu --------------------------------------------------------------
    lieu_n = norm(f"{offre.lieu} {offre.description[:400]}")
    if LIEUX_PROCHES.search(norm(offre.lieu)) or LIEUX_PROCHES.search(lieu_n[:120]):
        score += 8
        plus.append("Près de Lille")
    elif LIEUX_PARIS.search(norm(offre.lieu)):
        score += 3
    if TELETRAVAIL.search(texte_n):
        score += 2

    # --- Taille de l'entreprise -------------------------------------------
    if GRANDS_GROUPES.search(norm(offre.entreprise)):
        score -= 8
        moins.append("Très grand groupe : beaucoup de candidats")

    # --- Dates et durée ----------------------------------------------------
    if DEBUT_BON.search(texte_n):
        score += 6
        plus.append("Début ou durée compatibles (6 mois, début 2027)")
    if DEBUT_MAUVAIS.search(texte_n) and not DEBUT_BON.search(texte_n):
        score -= 10
        moins.append("Démarrage annoncé avant janvier 2027")
    if DUREE_COURTE.search(texte_n) and "6 mois" not in texte_n:
        score -= 5
        moins.append("Durée plus courte que 6 mois")

    # --- Langues -----------------------------------------------------------
    if LANGUE_RARE.search(texte_n):
        score -= 25
        moins.append("Langue étrangère exigée que tu ne parles pas")
    elif ANGLAIS_EXIGE.search(texte_n):
        score -= 6
        moins.append("Anglais courant exigé")

    offre.score = max(5, min(95, score))
    return offre


# ---------------------------------------------------------------------------
# Dédoublonnage
# ---------------------------------------------------------------------------
PRIORITE_SOURCE = {"France Travail": 0, "Adzuna": 1, "Indeed": 2, "HelloWork": 3, "LinkedIn": 4,
                   "Site carrière": 5, "Welcome to the Jungle": 2}


def dedoublonner(offres: list[Offre]) -> list[Offre]:
    """Garde une offre par clé : celle qui a une date vérifiée, puis la plus récente, puis la meilleure source."""
    meilleures: dict[str, Offre] = {}
    for o in offres:
        actuelle = meilleures.get(o.cle)
        if actuelle is None:
            meilleures[o.cle] = o
            continue

        def rang(x: Offre):
            return (x.date_publication is None, -(x.date_publication or date.min).toordinal(),
                    PRIORITE_SOURCE.get(x.source, 9), -len(x.description))

        garde, autre = (o, actuelle) if rang(o) < rang(actuelle) else (actuelle, o)
        if len(autre.description) > len(garde.description):
            garde.description = autre.description
        meilleures[o.cle] = garde
    return list(meilleures.values())


_MOTS_VIDES = set("stage stagiaire internship intern h f x m de du des la le les et en d l a au aux pour "
                  "fin etudes etude alternance alternant".split())


def _mots_titre(titre: str) -> set[str]:
    return {m for m in re.sub(r"[^a-z0-9]+", " ", norm(titre)).split() if m not in _MOTS_VIDES and len(m) > 1}


def _societe(nom: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\b(groupe|group|sa|sas|france)\b", " ", norm(nom)))


class Postulees(set):
    """Clés exactes des candidatures, plus une recherche approchée (même société, intitulé voisin)."""

    def __init__(self, cles=(), fiches=()):
        super().__init__(cles)
        self.fiches = list(fiches)              # (société simplifiée, mots du titre, libellé)

    def proche(self, offre: Offre) -> str | None:
        soc, mots = _societe(offre.entreprise), _mots_titre(offre.titre)
        if len(soc) < 3 or not mots:
            return None
        for s, m, libelle in self.fiches:
            if len(s) < 3 or not (s.startswith(soc) or soc.startswith(s)):
                continue
            commun = len(m & mots)
            if commun >= 2 and commun / min(len(m), len(mots)) >= 0.8:
                return libelle
        return None


def cles_deja_postulees(candidatures) -> Postulees:
    """Candidatures déjà saisies (DataFrame avec Entreprise, Poste et Date)."""
    if candidatures is None or len(candidatures) == 0:
        return Postulees()
    cles, fiches = [], []
    for _, r in candidatures.iterrows():
        ent, poste = str(r.get("Entreprise", "")).strip(), str(r.get("Poste", "")).strip()
        cles.append(Offre(source="", titre=poste, entreprise=ent).cle)
        quand = str(r.get("Date", "") or "")[:10]
        fiches.append((_societe(ent), _mots_titre(poste), f"{ent}, {poste}" + (f" le {quand}" if quand else "")))
    return Postulees(cles, fiches)


# ---------------------------------------------------------------------------
# Lanceur du CV (même modèle que celui du Gem)
# ---------------------------------------------------------------------------
def choisir_type(offre: Offre) -> str:
    texte = " " + norm(f"{offre.titre} {offre.entreprise} {offre.description}") + " "
    scores = {k: sum(1 for m in mots if m in texte) for k, mots in DOMAINES.items()}
    titre = " " + norm(offre.titre) + " "
    for k, mots in DOMAINES.items():          # un mot du titre compte double
        scores[k] += sum(1 for m in mots if m in titre)
    meilleur = max(scores, key=scores.get)
    # un seul indice dans un texte court (annonce tronquée) ne suffit pas
    return meilleur if scores[meilleur] >= 2 else "generique"


def choisir_mots_cles(offre: Offre, type_cv: str, max_car: int = 60) -> str:
    texte_n = norm(f"{offre.titre} {offre.description}")
    trouves = []
    libelles = {"VBA": "Excel (VBA)", "Excel": "Excel"}
    for libelle, motif in COMPETENCES:
        if motif is None:
            n = len(re.findall(r"(?<![\w&'’])R(?![\w&'’+#])", f"{offre.titre}\n{offre.description}"))
        else:
            n = len(re.findall(motif, texte_n))
        if n:
            trouves.append((n, libelles.get(libelle, libelle)))
    labels = [l for _, l in sorted(trouves, key=lambda x: -x[0])]
    if "Excel (VBA)" in labels and "Excel" in labels:
        labels.remove("Excel")
    choix = []
    for l in labels + MOTS_CLES_DEFAUT.get(type_cv, MOTS_CLES_DEFAUT["generique"]):
        if l in choix or len(choix) == 5:
            continue
        if len(" · ".join(choix + [l])) <= max_car:
            choix.append(l)
    return " · ".join(choix)


SIGLES = {"bi": "BI", "dax": "DAX", "sql": "SQL", "kpi": "KPI", "kpis": "KPI", "ia": "IA", "ai": "AI", "ml": "ML",
          "etl": "ETL", "crm": "CRM", "erp": "ERP", "rh": "RH", "it": "IT", "si": "SI", "vba": "VBA", "sas": "SAS",
          "r&d": "R&D", "nlp": "NLP", "llm": "LLM", "api": "API", "gcp": "GCP", "aws": "AWS", "sap": "SAP",
          "plm": "PLM", "gsi": "GSI", "rse": "RSE", "b2b": "B2B", "b2c": "B2C", "pme": "PME", "s&op": "S&OP",
          "powerbi": "Power BI", "python": "Python", "excel": "Excel", "tableau": "Tableau", "qlik": "Qlik",
          "data": "Data", "analyst": "Analyst", "analyste": "Analyste", "scientist": "Scientist",
          "engineer": "Engineer", "science": "Science"}


def _casse(t: str) -> str:
    """Casse propre : « STAGE - DATA ANALYST POWER BI » → « Stage - data analyst Power BI »."""
    lettres = [c for c in t if c.isalpha()]
    if lettres and sum(c.isupper() for c in lettres) / len(lettres) > 0.6:
        t = t.lower()
    def mot(m):
        w = m.group(0)
        cle = w.lower()
        if cle in SIGLES:
            return SIGLES[cle]
        return w
    t = re.sub(r"[A-Za-zÀ-ÿ&]+", mot, t)
    t = re.sub(r"(?i)\bpower bi\b", "Power BI", t)
    return t


def entreprise_cv(nom: str) -> str:
    """« EMOTORS » → « Emotors » ; les sigles courts (SNCF, CMA CGM) restent en capitales."""
    def mot(m):
        w = m.group(0)
        return w.capitalize() if w.isupper() and len(w) >= 6 else w
    return re.sub(r"[A-Za-zÀ-ÿ]+", mot, (nom or "").strip())


REGIONS_DEPTS = set(norm(x) for x in """France|Ile-de-France|Île-de-France|Auvergne-Rhône-Alpes|Hauts-de-France|Grand Est|
Normandie|Bretagne|Pays de la Loire|Centre-Val de Loire|Bourgogne-Franche-Comté|Nouvelle-Aquitaine|Occitanie|
Provence-Alpes-Côte d'Azur|Corse|Ain|Aisne|Allier|Alpes-de-Haute-Provence|Hautes-Alpes|Alpes-Maritimes|Ardèche|Ardennes|
Ariège|Aube|Aude|Aveyron|Bouches-du-Rhône|Calvados|Cantal|Charente|Charente-Maritime|Cher|Corrèze|Côte-d'Or|
Côtes-d'Armor|Creuse|Dordogne|Doubs|Drôme|Eure|Eure-et-Loir|Finistère|Gard|Haute-Garonne|Gers|Gironde|Hérault|
Ille-et-Vilaine|Indre|Indre-et-Loire|Isère|Jura|Landes|Loir-et-Cher|Loire|Haute-Loire|Loire-Atlantique|Loiret|Lot|
Lot-et-Garonne|Lozère|Maine-et-Loire|Manche|Marne|Haute-Marne|Mayenne|Meurthe-et-Moselle|Meuse|Morbihan|Moselle|
Nièvre|Nord|Oise|Orne|Pas-de-Calais|Puy-de-Dôme|Pyrénées-Atlantiques|Hautes-Pyrénées|Pyrénées-Orientales|Bas-Rhin|
Haut-Rhin|Rhône|Haute-Saône|Saône-et-Loire|Sarthe|Savoie|Haute-Savoie|Seine-Maritime|Seine-et-Marne|Yvelines|
Deux-Sèvres|Somme|Tarn|Tarn-et-Garonne|Var|Vaucluse|Vendée|Vienne|Haute-Vienne|Vosges|Yonne|Territoire de Belfort|
Essonne|Hauts-de-Seine|Seine-Saint-Denis|Val-de-Marne|Val-d'Oise|Télétravail|Remote|Hybride""".replace("\n", "").split("|"))


def ville_cv(lieu: str, *textes: str) -> str:
    """Ville lisible : « Champagne-au-Mont-d'Or, Lyon » → « Champagne-au-Mont-d'Or » ;
    « 2ème Arrondissement, Paris » → « Paris » ; « France » → ville citée dans le titre/l'annonce, sinon ""."""
    morceaux = [m.strip() for m in re.split(r"[,;|]", lieu or "") if m.strip()]
    for m in morceaux:
        m = re.sub(r"\s*\(\d{2,3}\)\s*$", "", m).strip()
        if re.search(r"(?i)arrondissement", m) or norm(m) in REGIONS_DEPTS:
            continue
        return m.title() if m.isupper() else m
    for t in textes:
        r = re.search(r"\b([A-ZÉÈ][\w'’-]+(?:[ -](?:[A-ZÉÈ][\w'’-]+|sur|sous|en|le|la|les|de|du)){0,4})\s*\((\d{2}|\d{3}|\d{5})\)", t or "")
        if r and norm(r.group(1)) not in REGIONS_DEPTS:
            return r.group(1).replace("-", "-")
    return ""


def titre_cv(titre: str) -> str:
    t = re.sub(r"\s*[\(\[]?\b(H/F|F/H|H/F/X|F/H/X|M/F|F/M|X/F/H)\b[\)\]]?", "", titre, flags=re.I)
    t = re.sub(r"\s*[-–|]\s*(r[ée]f\.?|ref)\s*[\w-]+$", "", t, flags=re.I)
    t = re.sub(r"(?i)\s*[\(\[]\s*(stage|stagiaire|internship)[^\)\]]*[\)\]]", "", t)
    t = re.sub(r"(?i)\s*[-–|]\s*(stage|stagiaire)\s*$", "", t)
    t = re.sub(r"(?i)\bbac\s*\+?\s*\d(\s*/\s*\d)?\s*[-–]?\s*", "", t)
    t = re.sub(r"\s+", " ", t).strip(" -–|")
    if not re.match(r"(?i)stage|stagiaire|internship", t):
        t = "Stage " + t[0].lower() + t[1:] if t else "Stage"
    t = re.sub(r"(?i)^stage\s*[-–:]\s*", "Stage ", t)
    t = re.sub(r"(?i)^stagiaire\s*[-–:]?\s*", "Stage ", t)
    t = _casse(t)
    return t[:1].upper() + t[1:]


MODELE_LANCEUR = '''# -*- coding: utf-8 -*-
# CV pour l'offre : {titre} | {entreprise}
TITRE = {titre!r}
ENTREPRISE = {entreprise!r}
TYPE = {type_cv!r}
MOTS_CLES = {mots_cles!r}

# ---- Ne rien modifier sous cette ligne ----
import os, runpy, sys
_ici = os.path.dirname(os.path.abspath(__file__))
_maison = os.path.expanduser("~")
_moteur = next((c for c in [
    os.path.join(_ici, "cv_final.py"),
    os.path.join(_maison, "Documents", "CV", "cv_final.py"),
    os.path.join(_maison, "OneDrive", "Documents", "CV", "cv_final.py"),
    os.path.join(_maison, "Downloads", "cv_final.py"),
] if os.path.exists(c)), None)
if _moteur is None:
    sys.exit("❌ cv_final.py introuvable : place-le dans Documents\\\\CV (ou dans le même dossier que ce fichier).")
runpy.run_path(_moteur, run_name="__main__", init_globals=dict(
    TITRE=TITRE, ENTREPRISE=ENTREPRISE, TYPE=TYPE, MOTS_CLES=MOTS_CLES))
'''


def lanceur(offre: Offre) -> tuple[str, str]:
    """(nom de fichier, contenu) du lanceur de CV pour cette offre."""
    type_cv = choisir_type(offre)
    entreprise = entreprise_cv(offre.entreprise) or "vos équipes"
    contenu = MODELE_LANCEUR.format(titre=titre_cv(offre.titre), entreprise=entreprise, type_cv=type_cv,
                                    mots_cles=choisir_mots_cles(offre, type_cv))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", norm(entreprise).title()).strip("_") or "Offre"
    return f"cv_offre_{slug}.py", contenu


# ---------------------------------------------------------------------------
# Dates relatives (« il y a 3 jours », « Publié à l'instant »…)
# ---------------------------------------------------------------------------
def date_relative(texte: str, reference: date) -> date | None:
    t = norm(texte)
    if re.search(r"a l.?instant|aujourd.?hui|today|just posted|nouveau|new\b|il y a \d+ (minute|heure)|\d+ (minutes?|hours?) ago", t):
        return reference
    if re.search(r"\bhier\b|yesterday", t):
        return reference - timedelta(days=1)
    m = re.search(r"(?:il y a|publie il y a|posted)\s*(plus de\s*|\+\s*de\s*|\+\s*)?(\d+)\s*(jour|day|semaine|week|mois|month)", t) \
        or re.search(r"(\+\s*)?(\d+)\+?\s*(jour|day|semaine|week|mois|month)s?\s*(?:ago)", t)
    if m:
        n = int(m.group(2))
        unite = m.group(3)
        jours = n if unite.startswith(("jour", "day")) else 7 * n if unite.startswith(("semaine", "week")) else 30 * n
        if m.group(1):          # « plus de 30 jours »
            jours += 1
        return reference - timedelta(days=jours)
    m = re.search(r"publiee? le (\d{1,2})/(\d{1,2})/(\d{4})", t)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def date_iso(valeur: str) -> date | None:
    try:
        return datetime.fromisoformat(valeur.replace("Z", "+00:00")).date()
    except Exception:
        try:
            return datetime.strptime(valeur[:10], "%Y-%m-%d").date()
        except Exception:
            return None


def lanceur_depuis_ligne(ligne: dict) -> tuple[str, str]:
    """Lanceur de CV à partir d'une ligne de l'onglet « Offres » (titre, entreprise, type, mots-clés)."""
    entreprise = entreprise_cv(str(ligne.get("Entreprise", ""))) or "vos équipes"
    contenu = MODELE_LANCEUR.format(titre=titre_cv(str(ligne.get("Titre", ""))), entreprise=entreprise,
                                    type_cv=str(ligne.get("Type_CV", "")) or "auto",
                                    mots_cles=str(ligne.get("Mots_cles", "")))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", norm(entreprise).title()).strip("_") or "Offre"
    return f"cv_offre_{slug}.py", contenu
