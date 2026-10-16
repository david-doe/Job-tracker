"""Analyse des candidatures : où ça bloque, et qu'est-ce qui distingue ce qui avance de ce qui échoue.

Tout part du Google Sheet. Les variables d'analyse sont déduites de l'intitulé du poste, de la ville,
de la date et des colonnes remplies par la lecture Gmail (Canal, Date_Reponse, Motif_Refus).
Les constats sont prudents : un écart n'est signalé que sur un nombre suffisant de candidatures.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import pandas as pd

# Date de mise en service du CV actuel (cv_final.py) : permet de comparer avant / après.
DATE_NOUVEAU_CV = date(2026, 10, 15)
SANS_REPONSE_APRES = 21  # jours sans nouvelles avant de compter une candidature comme « sans réponse »
REFUS_RAPIDE = 7         # un refus en moins de 7 jours vient en général d'un tri automatique ou d'un tri du CV
EFFECTIF_MIN = 5         # en dessous, on ne tire pas de conclusion sur un groupe

AVANCEES = {"Entretien RH", "Test Technique", "Entretien Manager", "Offre reçue"}


def _n(texte) -> str:
    texte = unicodedata.normalize("NFD", str(texte or "").lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def type_contrat(poste: str) -> str:
    p = _n(poste)
    if re.search(r"alternan|apprenti|apprentissage", p):
        return "Alternance"
    if re.search(r"stage|stagiaire|intern", p):
        return "Stage"
    if re.search(r"\bcdi\b|\bcdd\b|junior|confirme|senior", p):
        return "Emploi"
    return "Non précisé"


def famille(poste: str) -> str:
    p = _n(poste)
    regles = [
        ("IA / ML", r"\bia\b|\bai\b|intelligence artificielle|machine learning|\bml\b|llm|generative|nlp|deep learning|mlops"),
        ("Data Scientist", r"data scien|scientist|modelisation|statisticien"),
        ("Data Engineer", r"data engineer|ingenieur data|ingenieur\(e\) data|analytics engineer|etl"),
        ("BI / Reporting", r"\bbi\b|business intelligence|power bi|reporting|dataviz|tableau de bord|pilotage|controle de gestion"),
        ("Business Analyst", r"business analyst|analyste metier|\bba\b"),
        ("Data Analyst", r"data analy|analyste (de )?donnees|analyste data|analyst|analyste"),
    ]
    for nom, motif in regles:
        if re.search(motif, p):
            return nom
    return "Autre métier"


def zone(ville: str) -> str:
    v = _n(ville)
    if not v.strip():
        return "Non précisée"
    if re.search(r"lille|roubaix|tourcoing|villeneuve|lens|douai|valenciennes|dunkerque|arras|hauts|calais|boulogne|59|62", v):
        return "Hauts-de-France"
    if re.search(r"paris|ile-de-france|idf|nanterre|courbevoie|puteaux|boulogne-b|issy|massy|saint-denis|"
                 r"versailles|evry|cergy|villaroche|montereau|saint-quentin|meudon|arcueil|carrieres|poissy|92|93|94|95|78|91|77", v):
        return "Île-de-France"
    if re.search(r"canada|montreal|quebec|toronto|ottawa|vancouver|calgary", v):
        return "Canada"
    if re.search(r"remote|teletravail|a distance", v):
        return "Télétravail"
    return "Autres régions"


def _date(valeur):
    try:
        t = pd.to_datetime(valeur, errors="coerce")
        return None if pd.isna(t) else t.date()
    except Exception:
        return None


def enrichir(df: pd.DataFrame, aujourd_hui: date | None = None) -> pd.DataFrame:
    """Ajoute les variables d'analyse à chaque candidature."""
    aujourd_hui = aujourd_hui or date.today()
    d = df.copy()
    for col in ["Date", "Poste", "Ville", "Statut", "Canal", "Date_Reponse", "Motif_Refus", "Commentaires"]:
        if col not in d.columns:
            d[col] = ""
    d["date_cand"] = d["Date"].map(_date)
    d["date_rep"] = d["Date_Reponse"].map(_date)
    d["Type de contrat"] = d["Poste"].map(type_contrat)
    d["Métier"] = d["Poste"].map(famille)
    d["Zone"] = d["Ville"].map(zone)
    d["Canal"] = d["Canal"].replace("", pd.NA).fillna("Non renseigné")
    d["Version du CV"] = d["date_cand"].map(
        lambda x: "Nouveau CV" if x and x >= DATE_NOUVEAU_CV else ("Ancien CV" if x else "Date inconnue"))
    d["Mois"] = d["date_cand"].map(lambda x: x.strftime("%Y-%m") if x else "Inconnu")

    def issue(ligne):
        statut = str(ligne["Statut"])
        if statut in AVANCEES:
            return "Avancée"
        if statut == "Refus":
            return "Clôturée" if ligne["Motif_Refus"] == "Poste pourvu ou clôturé" else "Refus"
        if statut == "Abandon":
            return "Abandon"
        age = (aujourd_hui - ligne["date_cand"]).days if ligne["date_cand"] else None
        return "Sans réponse" if age is not None and age > SANS_REPONSE_APRES else "En attente"

    d["Issue"] = d.apply(issue, axis=1)
    d["Délai de réponse (j)"] = [
        (r - c).days if (r and c and r >= c) else None for r, c in zip(d["date_rep"], d["date_cand"])
    ]
    return d


def tableau(d: pd.DataFrame, dimension: str) -> pd.DataFrame:
    """Une ligne par groupe : effectif, issues et taux d'avancée."""
    base = d[d["Issue"] != "Abandon"]
    t = base.groupby(dimension).agg(
        Candidatures=("Issue", "size"),
        Avancées=("Issue", lambda s: (s == "Avancée").sum()),
        Refus=("Issue", lambda s: (s == "Refus").sum()),
        Clôturées=("Issue", lambda s: (s == "Clôturée").sum()),
        Sans_réponse=("Issue", lambda s: (s == "Sans réponse").sum()),
        En_attente=("Issue", lambda s: (s == "En attente").sum()),
        Délai_médian=("Délai de réponse (j)", "median"),
    )
    decidees = t["Candidatures"] - t["En_attente"]
    t["Taux d'avancée"] = (t["Avancées"] / decidees.where(decidees > 0)).round(3)
    t = t.rename(columns={"Sans_réponse": "Sans réponse", "En_attente": "En attente", "Délai_médian": "Délai médian (j)"})
    return t.sort_values("Candidatures", ascending=False)


@dataclass
class Constat:
    titre: str
    detail: str
    action: str
    poids: int  # pour trier : les constats les plus importants d'abord


def _pct(x: float) -> str:
    return f"{round(100 * x)} %"


def constats(d: pd.DataFrame) -> list[Constat]:
    """Règles simples et explicables. Chaque constat dit sur combien de candidatures il repose."""
    out: list[Constat] = []
    base = d[d["Issue"] != "Abandon"]
    decidees = base[base["Issue"] != "En attente"]
    n = len(decidees)
    if n == 0:
        return out
    avancees = (decidees["Issue"] == "Avancée").sum()
    refus = decidees[decidees["Issue"].isin(["Refus", "Clôturée"])]

    # 1. Taux d'avancée global
    if n >= EFFECTIF_MIN and avancees == 0:
        out.append(Constat(
            "Aucune candidature n'a passé le premier tri",
            f"0 entretien ou test sur {n} candidatures qui ont eu une issue.",
            "Le blocage se situe au tri du CV, pas en entretien : travaille le ciblage et le CV avant le volume.",
            100))
    elif n >= EFFECTIF_MIN:
        out.append(Constat(
            f"{_pct(avancees / n)} de candidatures passent le premier tri",
            f"{avancees} entretien(s) ou test(s) sur {n} candidatures qui ont eu une issue.",
            "Un taux sous 10 % en stage data signale un problème de ciblage ou de CV ; au-dessus de 15 %, le CV fonctionne.",
            60))

    # 2. Refus rapides = tri automatique ou tri du CV en quelques secondes
    delais = refus["Délai de réponse (j)"].dropna()
    if len(delais) >= EFFECTIF_MIN:
        rapides = (delais <= REFUS_RAPIDE).mean()
        if rapides >= 0.4:
            out.append(Constat(
                f"{_pct(rapides)} des refus arrivent en moins de {REFUS_RAPIDE} jours",
                f"Délai médian de refus : {int(delais.median())} jours, sur {len(delais)} refus datés.",
                "Un refus aussi rapide vient d'un tri automatique ou d'une lecture de quelques secondes : "
                "mots-clés de l'offre absents, intitulé de poste trop éloigné, ou critère bloquant (lieu, dates, niveau).",
                90))

    # 3. Offres déjà pourvues : on postule trop tard
    clos = (decidees["Issue"] == "Clôturée").sum()
    if clos >= 3 and clos / n >= 0.2:
        out.append(Constat(
            f"{_pct(clos / n)} des candidatures tombent sur un poste déjà pourvu",
            f"{clos} offres clôturées ou pourvues sur {n}.",
            "Tu postules trop tard : vise les offres publiées depuis moins de 3 jours (alertes quotidiennes) "
            "et laisse de côté celles qui ont plus de 3 semaines.",
            85))

    # 4. Groupes qui échouent nettement plus que les autres
    taux_global = avancees / n if n else 0
    for dimension in ["Type de contrat", "Métier", "Zone", "Canal"]:
        t = tableau(base, dimension)
        for groupe, ligne in t.iterrows():
            effectif = ligne["Candidatures"] - ligne["En attente"]
            if groupe in {"Non précisé", "Non précisée", "Non renseigné", "Autre métier"} or effectif < EFFECTIF_MIN:
                continue
            taux_clos = ligne["Clôturées"] / effectif
            if dimension == "Type de contrat" and groupe == "Alternance" and (ligne["Avancées"] == 0):
                out.append(Constat(
                    f"Alternance : {int(effectif)} candidatures, aucune avancée",
                    f"Dont {int(ligne['Clôturées'])} postes déjà pourvus.",
                    "Les alternances se pourvoient surtout entre mars et juillet. Hors de cette période, "
                    "et si ton master n'est pas en rythme alternance, concentre-toi sur les stages.",
                    80))
            elif taux_clos >= 0.5 and ligne["Clôturées"] >= 3:
                out.append(Constat(
                    f"{dimension} « {groupe} » : {_pct(taux_clos)} des offres déjà pourvues",
                    f"{int(ligne['Clôturées'])} clôturées sur {int(effectif)}.",
                    f"Dans le groupe « {groupe} », les offres partent vite : postule plus tôt"
                    + (" ou passe par le site carrière de l'entreprise." if dimension == "Canal" else "."),
                    70))
            elif taux_global > 0 and ligne["Avancées"] == 0 and effectif >= 2 * EFFECTIF_MIN:
                out.append(Constat(
                    f"{dimension} « {groupe} » : {int(effectif)} candidatures, aucune avancée",
                    f"Alors que {_pct(taux_global)} des candidatures avancent en moyenne.",
                    f"Revois l'adéquation entre ton profil et « {groupe} », ou mets moins d'effort sur ce groupe.",
                    65))

    # 5. Dispersion des métiers visés
    metiers = base["Métier"].value_counts()
    metiers = metiers[metiers.index != "Autre métier"]
    if len(base) >= 10 and len(metiers) >= 4 and metiers.iloc[0] / metiers.sum() < 0.5:
        out.append(Constat(
            f"Candidatures dispersées sur {len(metiers)} métiers",
            "Aucun métier ne représente la moitié de tes candidatures : "
            + ", ".join(f"{m} ({c})" for m, c in metiers.head(4).items()) + ".",
            "Choisis un métier principal (par exemple Data Analyst) pour au moins 70 % de tes candidatures : "
            "un CV et un discours ciblés convainquent mieux.",
            75))

    # 6. Motifs de refus
    motifs = refus["Motif_Refus"].replace("", pd.NA).dropna()
    motifs = motifs[motifs != "Poste pourvu ou clôturé"]
    if len(motifs) >= EFFECTIF_MIN:
        part_profil = (motifs == "Profil ne correspond pas").mean()
        if part_profil >= 0.4:
            out.append(Constat(
                f"{_pct(part_profil)} des refus motivés citent un profil qui ne correspond pas",
                f"Sur {len(motifs)} refus dont le motif a été lu.",
                "Relis ces offres : quel critère manquait (outil, secteur, niveau, durée) ? "
                "Écarte les offres où il te manque une exigence clé.",
                70))

    # 7. Sans réponse
    sans = (decidees["Issue"] == "Sans réponse").sum()
    if sans >= EFFECTIF_MIN and sans / n >= 0.3:
        out.append(Constat(
            f"{_pct(sans / n)} des candidatures restent sans réponse après {SANS_REPONSE_APRES} jours",
            f"{sans} candidatures sans nouvelles.",
            "Relance une fois, et quand c'est possible écris directement au manager sur LinkedIn.",
            50))

    # 8. Avant / après le nouveau CV
    t_cv = tableau(base, "Version du CV")
    if {"Ancien CV", "Nouveau CV"} <= set(t_cv.index):
        nouveau = t_cv.loc["Nouveau CV"]
        effectif = nouveau["Candidatures"] - nouveau["En attente"]
        taux_nouveau = nouveau["Taux d'avancée"] if pd.notna(nouveau["Taux d'avancée"]) else 0
        taux_ancien = t_cv.loc["Ancien CV", "Taux d'avancée"]
        taux_ancien = taux_ancien if pd.notna(taux_ancien) else 0
        if effectif >= 10:
            out.append(Constat(
                f"Nouveau CV : {_pct(taux_nouveau)} d'avancée contre {_pct(taux_ancien)} avant",
                f"Sur {int(effectif)} candidatures envoyées avec le nouveau CV.",
                "Si l'écart reste nul après 20 candidatures, le blocage vient du choix des offres plutôt que du CV.",
                55))
        else:
            out.append(Constat(
                "Nouveau CV : pas encore assez de recul",
                f"{int(effectif)} candidature(s) avec une issue depuis le {DATE_NOUVEAU_CV:%d/%m}. Il en faut une dizaine pour comparer.",
                "Continue à tout suivre dans l'app : la comparaison s'affichera ici automatiquement.",
                10))

    return sorted(out, key=lambda c: -c.poids)
