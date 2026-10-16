"""Logique des relances : qui relancer, par quel canal, avec quel message.

Module sans dépendance à Streamlit pour pouvoir être testé seul.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from urllib.parse import quote, urlencode

import pandas as pd

# Statuts pour lesquels on ne relance plus.
STATUTS_CLOS = {"Refus", "Abandon", "Offre reçue"}

# Nombre maximum de relances après une candidature sans réponse.
MAX_RELANCES = 2

# Délai avant la relance suivante, selon le numéro de la relance envoyée.
DELAI_APRES_RELANCE = {1: 7, 2: 10}

# Villes où l'on écrit en anglais par défaut (le reste du Canada et l'international).
VILLES_ANGLAIS = {
    "toronto", "vancouver", "calgary", "edmonton", "ottawa", "winnipeg",
    "waterloo", "kitchener", "mississauga", "hamilton", "halifax", "victoria",
    "regina", "saskatoon", "london", "dublin", "new york", "boston", "remote",
}
VILLES_FRANCAIS = {
    "montreal", "quebec", "laval", "gatineau", "sherbrooke", "trois-rivieres",
    "longueuil", "levis", "saguenay",
}

CANAL_EMAIL = "Email"
CANAL_LINKEDIN = "LinkedIn"


@dataclass
class Recommandation:
    canal: str
    raison: str


def _normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFKD", str(texte or "")).encode("ascii", "ignore").decode()
    return texte.strip().lower()


def _vide(valeur) -> bool:
    return valeur is None or (isinstance(valeur, float) and pd.isna(valeur)) or str(valeur).strip() == ""


def nb_relances(ligne) -> int:
    try:
        return int(float(ligne.get("Nb_Relances") or 0))
    except (TypeError, ValueError):
        return 0


def relances_dues(df: pd.DataFrame, aujourd_hui: date | None = None) -> pd.DataFrame:
    """Candidatures actives dont la date de relance est atteinte, les plus en retard d'abord."""
    if df.empty or "Date_Relance" not in df.columns:
        return df.iloc[0:0]
    aujourd_hui = pd.Timestamp(aujourd_hui or date.today()).normalize()
    dates = pd.to_datetime(df["Date_Relance"], errors="coerce")
    actives = ~df["Statut"].isin(STATUTS_CLOS)
    dues = df[actives & dates.notna() & (dates <= aujourd_hui)].copy()
    dues["_date_relance"] = dates[dues.index]
    return dues.sort_values("_date_relance")


def langue_suggeree(ville: str) -> str:
    v = _normaliser(ville)
    if any(nom in v for nom in VILLES_FRANCAIS):
        return "FR"
    if any(nom in v for nom in VILLES_ANGLAIS):
        return "EN"
    return "FR"


def canal_recommande(ligne) -> Recommandation:
    email = ligne.get("Contact_Email", "")
    profil = ligne.get("Contact_LinkedIn", "")
    lien = str(ligne.get("Lien_Offre", "") or "")
    if not _vide(email):
        return Recommandation(CANAL_EMAIL, "Contact email connu : c'est le canal le plus direct et il garde une trace.")
    if not _vide(profil):
        return Recommandation(CANAL_LINKEDIN, "Pas d'email, mais le profil LinkedIn du contact est connu.")
    if "linkedin.com" in lien.lower():
        return Recommandation(CANAL_LINKEDIN, "Offre publiée sur LinkedIn : écris à la personne qui l'a publiée ou au recruteur.")
    return Recommandation(
        CANAL_LINKEDIN,
        "Aucun contact connu : trouve le recruteur sur LinkedIn, ou ajoute son email pour passer par mail.",
    )


def _date_lisible(valeur, langue: str) -> str:
    d = pd.to_datetime(valeur, errors="coerce")
    if pd.isna(d):
        return ""
    if langue == "EN":
        return d.strftime("%B %-d")
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
            "août", "septembre", "octobre", "novembre", "décembre"]
    return f"{d.day} {mois[d.month - 1]}"


def _salutation(nom: str, langue: str) -> str:
    if _vide(nom):
        return "Hello," if langue == "EN" else "Bonjour,"
    return f"Hello {nom.strip()}," if langue == "EN" else f"Bonjour {nom.strip()},"


def _etape(statut: str) -> str:
    s = _normaliser(statut)
    if "entretien" in s:
        return "entretien"
    if "test" in s:
        return "test"
    return "candidature"


def objet_mail(ligne, langue: str) -> str:
    poste = str(ligne.get("Poste", "")).strip()
    etape = _etape(ligne.get("Statut", ""))
    if langue == "EN":
        return {
            "candidature": f"Following up on my application – {poste}",
            "entretien": f"Thank you – {poste} interview",
            "test": f"Technical assessment follow-up – {poste}",
        }[etape]
    return {
        "candidature": f"Suivi de ma candidature – {poste}",
        "entretien": f"Suite à notre entretien – {poste}",
        "test": f"Suivi du test technique – {poste}",
    }[etape]


def corps_mail(ligne, langue: str, signature: str) -> str:
    entreprise = str(ligne.get("Entreprise", "")).strip()
    poste = str(ligne.get("Poste", "")).strip()
    date_cand = _date_lisible(ligne.get("Date", ""), langue)
    salut = _salutation(ligne.get("Contact_Nom", ""), langue)
    etape = _etape(ligne.get("Statut", ""))
    numero = nb_relances(ligne) + 1
    derniere = numero >= MAX_RELANCES

    if langue == "EN":
        quand = f" on {date_cand}" if date_cand else ""
        if etape == "entretien":
            corps = (
                f"Thank you again for taking the time to discuss the {poste} position with me. "
                f"Our conversation confirmed my interest in joining {entreprise}, especially the data work your team is doing.\n\n"
                "I would be glad to hear about the next steps whenever you have an update, "
                "and I remain available if you need any additional information."
            )
        elif etape == "test":
            corps = (
                f"I recently completed the technical assessment for the {poste} position and wanted to follow up. "
                "I would appreciate any feedback on my submission and an idea of the next steps in the process."
            )
        elif derniere:
            corps = (
                f"I am following up one last time on my application for the {poste} position, submitted{quand}. "
                f"I remain very interested in joining {entreprise}. If the position has been filled, "
                "a short reply would help me a lot, and I would be happy to be considered for future openings."
            )
        else:
            corps = (
                f"I applied for the {poste} position at {entreprise}{quand} and wanted to confirm that my application was received. "
                "My background in data analysis and decision support (Master's in Information Systems) matches the role well, "
                "and I would welcome the chance to discuss how I could contribute to your team.\n\n"
                "Would you have a moment to let me know where the process stands?"
            )
        return f"{salut}\n\n{corps}\n\nBest regards,\n{signature}"

    quand = f" le {date_cand}" if date_cand else ""
    if etape == "entretien":
        corps = (
            f"Je vous remercie encore pour notre échange au sujet du poste de {poste}. "
            f"Il a confirmé mon intérêt pour {entreprise} et pour les sujets data portés par votre équipe.\n\n"
            "Je serais ravi de connaître les prochaines étapes dès que vous aurez des nouvelles, "
            "et je reste disponible pour tout complément d'information."
        )
    elif etape == "test":
        corps = (
            f"J'ai récemment rendu le test technique pour le poste de {poste} et je me permets de revenir vers vous. "
            "Je serais preneur d'un retour sur mon travail ainsi que des prochaines étapes du processus."
        )
    elif derniere:
        corps = (
            f"Je me permets une dernière relance au sujet de ma candidature au poste de {poste}, envoyée{quand}. "
            f"Je reste très intéressé par {entreprise}. Si le poste est pourvu, un bref retour m'aiderait beaucoup, "
            "et je serais heureux que mon profil soit conservé pour de futures opportunités."
        )
    else:
        corps = (
            f"J'ai postulé{quand} au poste de {poste} chez {entreprise} et je souhaitais m'assurer que ma candidature vous était bien parvenue. "
            "Mon parcours en analyse de données et aide à la décision (Master SIAD) correspond aux missions du poste, "
            "et j'aimerais échanger sur la façon dont je pourrais contribuer à votre équipe.\n\n"
            "Auriez-vous un moment pour me dire où en est le processus ?"
        )
    return f"{salut}\n\n{corps}\n\nBien cordialement,\n{signature}"


def message_linkedin(ligne, langue: str, prenom: str) -> str:
    """Message court, compatible avec la limite de 300 caractères d'une invitation LinkedIn."""
    entreprise = str(ligne.get("Entreprise", "")).strip()
    poste = str(ligne.get("Poste", "")).strip()
    etape = _etape(ligne.get("Statut", ""))
    nom = ligne.get("Contact_Nom", "")
    if langue == "EN":
        salut = f"Hi {nom.strip()}," if not _vide(nom) else "Hi,"
        if etape == "entretien":
            texte = f"{salut} thank you again for our conversation about the {poste} role at {entreprise}. I'd be glad to hear about next steps whenever you have news. {prenom}"
        else:
            texte = f"{salut} I recently applied for the {poste} role at {entreprise} and I'm very interested in the team's data work. Could you tell me where the process stands? Thanks! {prenom}"
    else:
        salut = f"Bonjour {nom.strip()}," if not _vide(nom) else "Bonjour,"
        if etape == "entretien":
            texte = f"{salut} merci encore pour notre échange sur le poste de {poste} chez {entreprise}. Je serais ravi de connaître la suite dès que vous aurez des nouvelles. {prenom}"
        else:
            texte = f"{salut} j'ai postulé au poste de {poste} chez {entreprise} et le sujet m'intéresse beaucoup. Pourriez-vous me dire où en est le processus ? Merci ! {prenom}"
    return texte[:300]


def lien_gmail(destinataire: str, objet: str, corps: str) -> str:
    params = urlencode({"view": "cm", "fs": "1", "to": destinataire or "", "su": objet, "body": corps}, quote_via=quote)
    return f"https://mail.google.com/mail/?{params}"


def lien_recherche_linkedin(entreprise: str) -> str:
    mots = f"{entreprise} recruiter talent acquisition"
    return f"https://www.linkedin.com/search/results/people/?keywords={quote(mots)}"


def prochaine_date(numero_envoye: int, aujourd_hui: date | None = None) -> date:
    aujourd_hui = aujourd_hui or date.today()
    return aujourd_hui + timedelta(days=DELAI_APRES_RELANCE.get(numero_envoye, 14))


def note_historique(texte_existant: str, action: str, aujourd_hui: date | None = None) -> str:
    aujourd_hui = aujourd_hui or date.today()
    ligne = f"[{aujourd_hui.isoformat()}] {action}"
    existant = "" if _vide(texte_existant) else str(texte_existant).rstrip()
    return f"{existant}\n{ligne}" if existant else ligne


EMAIL_VALIDE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def email_valide(email: str) -> bool:
    return bool(EMAIL_VALIDE.match(str(email or "").strip()))


def date_iso(valeur) -> str:
    if isinstance(valeur, (date, datetime)):
        return valeur.strftime("%Y-%m-%d")
    d = pd.to_datetime(valeur, errors="coerce")
    return "" if pd.isna(d) else d.strftime("%Y-%m-%d")
