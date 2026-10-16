"""Synchronisation Gmail -> Google Sheet.

Lit les réponses aux candidatures (refus, offres clôturées, tests, entretiens,
propositions) et met à jour les candidatures saisies dans le Sheet. Ne crée jamais
de candidature : la liste reste celle que l'utilisateur saisit lui-même.
Un accusé de réception ne sert qu'à récupérer un contact joignable pour les relances. Chaque mail traité est noté dans l'onglet « Mails_traites »
pour ne jamais être compté deux fois.

Lecture en IMAP avec le même mot de passe d'application que l'envoi des relances.
Les mails ne sont pas marqués comme lus.
"""

from __future__ import annotations

import email
import html
import imaplib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

# ---------------------------------------------------------------------------
# Réglages
# ---------------------------------------------------------------------------
JOURS_PREMIERE_SYNCHRO = 90  # au premier lancement, on remonte ce nombre de jours
DELAI_RELANCE = 7

# Alertes d'offres et newsletters : jamais des réponses à une candidature.
EXPEDITEURS_IGNORES = [
    r"jobalerts?-noreply@linkedin\.com", r"groups-noreply@linkedin\.com", r"messages-noreply@linkedin\.com",
    r"@(match|jobalert)\.indeed\.com", r"(notification|alerte|newsletter)@emails\.hellowork\.com",
    r"meteojob", r"adzuna", r"jooble", r"glassdoor", r"welcometothejungle\.com$", r"@news\.", r"@info\.",
]
OBJETS_IGNORES = [
    r"nouvelles? offres?", r"offres? d.emploi suppl", r"recrute (un|une|au poste)", r"vous pourriez convenir",
    r"postulez (maintenant|en quelques)", r"offres similaires", r"votre alerte",
]

LIBELLES = {
    "confirmation": "Candidature reçue",
    "refus": "Refus",
    "cloture": "Offre clôturée",
    "test": "Invitation à un test",
    "entretien": "Invitation à un entretien",
    "offre": "Proposition reçue",
    "cv_consulte": "CV consulté",
}

# Mots qui rendent une phrase hypothétique (« si votre profil est retenu, un entretien... »).
CONDITIONNEL = re.compile(
    r"\b(si|if|lorsque|au cas ou|dans le cas ou|le cas echeant|serez|pourrez|pourra|pourrait|"
    r"may|might|eventuel\w*|susceptibles?|chances de|ameliorer)\b"
)

MOTIFS = {
    "offre": [
        r"promesse d.embauche", r"proposition d.embauche", r"proposition de stage",
        r"le plaisir de vous (annoncer|informer|confirmer) que (votre candidature|vous) (a ete|avez ete|etes) (retenue?|selectionnee?)",
        r"pleased to offer", r"delighted to offer", r"offer letter", r"convention de stage",
    ],
    "cloture": [
        r"poste (vient d.etre |a ete |est )?pourvu", r"recrutement (est )?clos", r"procedure de recrutement est (desormais )?close",
        r"n.est plus disponible", r"offre (a ete )?(cloturee|fermee|expiree|retiree)", r"position has been filled",
        r"no longer (available|open)", r"ne sera donc pas etudiee",
    ],
    "refus": [
        r"\bregret", r"malheureusement", r"unfortunately", r"n.a pas ete retenue?", r"pas ete retenue?", r"ne pas retenir",
        r"(suite|reponse) favorable", r"ne (lui )?donnera pas (de )?suite", r"ne donnerons pas (de )?suite",
        r"pas (pu )?donner (de )?suite", r"n.y donnera pas suite", r"malgre (les points positifs|tout l.interet|l.interet|la qualite)", r"not (been )?selected", r"not to move forward", r"move forward with other",
        r"autres? (candidats|profils)", r"other candidates", r"ne correspond(ant)? pas", r"etape suivante de son processus",
        r"will not be progressing", r"not be taking your application",
    ],
    "test": [
        r"test technique", r"test en ligne", r"tests? psychotechniques?", r"cas pratique", r"etude de cas", r"case study",
        r"assessment", r"coding (challenge|test|game)", r"hackerrank", r"codingame", r"codility", r"testgorilla",
        r"hirevue", r"pymetrics", r"evaluation en ligne",
    ],
    "entretien": [r"entretien", r"interview", r"vous rencontrer", r"echange (telephonique|en visio|visio)", r"appel telephonique"],
    "cv_consulte": [r"cv a ete consulte", r"a consulte votre (cv|candidature|profil)", r"viewed your (application|profile|cv)"],
    "confirmation": [
        r"bien recu votre candidature", r"avons bien recu", r"accusons reception", r"accuse de reception",
        r"bien ete (recue|transmise|envoyee|enregistree|prise en compte)", r"votre candidature (a bien ete|est bien)",
        r"merci (d.avoir|pour avoir) postule", r"merci pour votre candidature", r"nous vous remercions (pour|de) votre candidature",
        r"thank you for (applying|your application|your interest)", r"(we have |we.ve )?received your application",
        r"application (has been )?(received|submitted)", r"candidatures? via indeed", r"candidature (a ete )?envoyee",
    ],
}
# Un entretien ou un test n'est retenu que s'il y a une vraie invitation.
INVITATION = re.compile(
    r"disponibilit|creneau|calendly|planifier|programmer|convier|convions|vous inviter|vous invitons|invite you|"
    r"fixer (un|une)|organiser (un|une)|schedule|availability|book a|lien (suivant|ci-dessous)|merci de (compléter|completer|realiser)|"
    r"please complete|date limite|deadline"
)
# « entretien » ou « interview » seuls ne suffisent pas : les newsletters et publicités en parlent aussi.
CONTEXTE_CANDIDATURE = re.compile(
    r"candidature|postule|application|applying|applied|recrutement|recruitment|talent acquisition|"
    r"votre profil|your profile|processus de selection|selection process"
)

# ---------------------------------------------------------------------------
# Outils texte
# ---------------------------------------------------------------------------
def norm(texte: str) -> str:
    texte = html.unescape(texte or "").replace("​", "").replace("’", "'")
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", texte).strip()


def _html_vers_texte(contenu: str) -> str:
    contenu = re.sub(r"(?is)<(script|style).*?</\1>", " ", contenu)
    contenu = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", contenu)
    contenu = re.sub(r"<[^>]+>", " ", contenu)
    return html.unescape(contenu)


def _nettoyer(texte: str) -> str:
    texte = html.unescape(texte or "").replace("​", "").replace("\xa0", " ")
    texte = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", texte)  # liens markdown
    texte = re.sub(r"https?://\S+", " ", texte)
    texte = re.sub(r"[|]+", " ", texte)
    return re.sub(r"[ \t]+", " ", texte)


def _trouve(motifs: list[str], texte: str, ignorer_conditionnel: bool = False) -> bool:
    for motif in motifs:
        for m in re.finditer(motif, texte):
            if not ignorer_conditionnel:
                return True
            avant = texte[max(0, m.start() - 90): m.start()]
            phrase = re.split(r"[.!?;:]\s", avant)[-1]
            if not CONDITIONNEL.search(phrase):
                return True
    return False


# ---------------------------------------------------------------------------
# Mails
# ---------------------------------------------------------------------------
@dataclass
class Mail:
    message_id: str
    date: date
    expediteur_nom: str
    expediteur_adresse: str
    objet: str
    corps: str

    @property
    def texte(self) -> str:
        return norm(f"{self.objet} \n {self.corps[:6000]}")


def est_ignore(mail: Mail) -> bool:
    adresse = mail.expediteur_adresse.lower()
    if any(re.search(m, adresse) for m in EXPEDITEURS_IGNORES):
        return True
    objet = norm(mail.objet)
    return any(re.search(m, objet) for m in OBJETS_IGNORES) and "candidature" not in objet


def classer(mail: Mail) -> str | None:
    """Renvoie la catégorie du mail, ou None s'il ne concerne pas une candidature."""
    if est_ignore(mail):
        return None
    t = mail.texte
    if not CONTEXTE_CANDIDATURE.search(t):
        return None
    if _trouve(MOTIFS["offre"], t, ignorer_conditionnel=True):
        return "offre"
    if _trouve(MOTIFS["cloture"], t):
        return "cloture"
    if _trouve(MOTIFS["refus"], t):
        return "refus"
    if _trouve(MOTIFS["test"], t, ignorer_conditionnel=True) and INVITATION.search(t):
        return "test"
    if _trouve(MOTIFS["entretien"], t, ignorer_conditionnel=True) and INVITATION.search(t):
        return "entretien"
    if _trouve(MOTIFS["cv_consulte"], t):
        return "cv_consulte"
    if _trouve(MOTIFS["confirmation"], t):
        return "confirmation"
    return None


# ---------------------------------------------------------------------------
# Entreprise et poste
# ---------------------------------------------------------------------------
PLATEFORMES = {
    "myworkday.com", "profils.org", "smartrecruiters.com", "welcomekit.co", "talent-soft.com", "teamtailor-mail.com",
    "digitalrecruiters.com", "csod.com", "altays-progiciels.com", "indeed.com", "hellowork.com", "jobaffinity.fr",
    "lever.co", "greenhouse.io", "successfactors.eu", "successfactors.com", "taleo.net", "icims.com", "recruitee.com",
    "flatchr.io", "gmail.com", "workablemail.com", "jobvite.com", "beetween.com",
}
MOTS_GENERIQUES = {
    "noreply", "no-reply", "no_reply", "ne_pas_repondre", "nepasrepondre", "ne-pas-repondre", "notification",
    "notifications", "recrutement", "recruitment", "careers", "career", "jobs", "job", "emploi", "talent", "talents",
    "rh", "hr", "automate", "candidature", "candidatures", "contact", "info", "mail", "message", "send-only", "do-not-reply",
    "donotreply", "indeedapply", "indeed", "hellowork", "smartrecruiters", "workday", "team", "equipe", "the",
}


NOM = r"[A-Z0-9][\w&.'’-]*(?:(?:\s+(?:de|du|des|et|&)\s+(?:l['’])?|\s+|\s*['’])[A-Z0-9][\w&.'’-]*){0,4}"


def _propre(nom: str) -> str:
    nom = re.sub(r"[​\"“”«»]", "", nom).strip()
    nom = re.split(r"(?<=[a-z]{3})\.(?=[A-Z])|\.\s", nom)[0]
    nom = re.sub(r"\s+-\s+(CDD|CDI|Stage|Alternance).*$", "", nom, flags=re.I)
    nom = re.sub(r"\s+(Careers|Recrutement|Recruitment|Talent Acquisition|RH)$", "", nom, flags=re.I)
    return nom.strip(" -–—:,.")


def _valide(nom: str) -> bool:
    n = norm(nom)
    return len(n) >= 2 and n not in MOTS_GENERIQUES | {"notre", "nous", "votre", "vos", "suite", "retour candidature"} \
        and not n.startswith(("suite a", "votre ", "your ", "retour ", "update "))


def extraire_entreprise(mail: Mail) -> str:
    objet = _nettoyer(mail.objet).strip()
    corps = _nettoyer(mail.corps)[:3000]
    local, _, domaine = mail.expediteur_adresse.lower().partition("@")
    racine = ".".join(domaine.split(".")[-2:])

    motifs_objet = [
        r"r[ée]ponse de (?P<n>.+?) pour l.offre",
        r"^(?P<n>[\w&'. ]{2,30}?)\s+-\s+(?:[Ss]uite|[Rr]etour|[Vv]otre|[Yy]our|[Uu]pdate)",
        r"^\{?(?P<n>[A-Z][\w&'. -]{1,30}?)\s+RECRUTEMENT\}?",
        r"^(?P<n>[A-Z][\w&'. ]{1,30}?)\s+Careers\b",
        r"(?:postul[ée] chez|applying (?:to|at)|application (?:to|at)|candidature chez)\s+(?P<n>" + NOM + ")",
        r"(?:candidature pour|candidature chez|your)\s+(?P<n>" + NOM + r")(?:\s+Application)?$",
    ]
    fin_objet = [
        r"au sein (?:du|de la|de l'|de|d')\s*(?P<n>" + NOM + ")$",
        r"[–—-]\s*(?P<n>" + NOM + ")$",
        r"\bchez\s+(?P<n>" + NOM + ")",
    ]
    for motif in motifs_objet:
        m = re.search(motif, objet)
        if m and _valide(_propre(m.group("n"))):
            return _propre(m.group("n"))
    if racine in PLATEFORMES:
        morceaux = [c for c in re.split(r"[-_.]", local)
                    if c and c not in MOTS_GENERIQUES and not re.search(r"\d", c) and len(c) >= 3]
        morceaux = [re.sub(r"(jobs|careers|recrute|rh)$", "", c) for c in morceaux]
        if morceaux and len(morceaux[0]) >= 4:
            return morceaux[0].capitalize()

    for motif in fin_objet:
        m = re.search(motif, objet)
        if m and _valide(_propre(m.group("n"))):
            return _propre(m.group("n"))

    motifs_corps = [
        r"dans l'entreprise (?P<n>[^.\n|]{1,40}?)\s*\.",
        r"(?:int[ée]r[êe]t (?:port[ée] )?(?:à|a|pour)|interest in|au sein (?:du|de la|de l'|de|d'))\s*(?P<n>" + NOM + ")",
        r"(?:chez|rejoindre|position at)\s+(?P<n>" + NOM + ")",
        r"(?P<n>" + NOM + r") Talent Acquisition",
    ]
    for motif in motifs_corps:
        m = re.search(motif, corps)
        if m and _valide(_propre(m.group("n"))):
            return _propre(m.group("n"))

    if domaine and racine not in PLATEFORMES:
        partie = domaine.split(".")[-2]
        if partie not in {"exemple", "example", "mail"}:
            return partie.replace("-", " ").title()
    nom = _propre(mail.expediteur_nom)
    if nom and _valide(nom) and "@" not in nom:
        return nom
    return ""


PREFIXES_POSTE = re.compile(r"^(?:(?:l['’]offre|le poste|offre|poste|position)\s*(?:de |d'|of |:)?|de |d')\s*", re.I)
FIN_POSTE = re.compile(
    r"\s+(?:au sein|chez|dans l'entreprise|at|n'est plus|est clos)\b.*$|\s*[(\[]?\s*(?:F/H|H/F)\b.*$|"
    r"\s+-?\s*\d{4}-\d{2,}.*$|\s*\(\d{4}-\d+\).*$|\s*\((?:R|Réf\.?|Ref\.?)\s*\d+.*$|\s+-\s+stage$", re.I)


def _poste_propre(poste: str) -> str:
    poste = PREFIXES_POSTE.sub("", poste.strip(" -–:.,\"«»“”"))
    poste = re.sub(r"^(?:Ref\.?|Réf\.?)\s*[\w-]*\d[\w-]*\s*[,–-]?\s*", "", poste, flags=re.I)
    poste = FIN_POSTE.sub("", poste)
    return poste.strip(" -–:.,\"«»“”")


def extraire_poste(mail: Mail) -> str:
    objet = _nettoyer(mail.objet).strip()
    corps = _nettoyer(mail.corps)[:2000]
    precis = [
        r"examin[ée] votre candidature avec attention pour ?:\s*(?P<p>.+?)(?:\s+-\s+\d{4}-|\s+Malgr|$)",
        r"r[ée]ponse à l'offre ?:?\s*[\"«“]\s*(?P<p>[^\"»”]+)",
        r"(?:au poste de|pour le poste(?: de)?|le poste de|sur le poste|for the position of|for the role of|"
        r"application for the|postul[ée] (?:à|a) l'offre|pour l'offre|sur l'offre|l'offre de|candidature pour le poste)\s*"
        r"(?:(?:Ref\.?|Réf\.?)\s*[\w-]+\s*,?\s*)?[\"«“]?\s*(?P<p>[^\"»”\n]{4,140}?)\s*[\"»”]?(?=\s*(?:,|\.(?:\s|$)|\.(?=[A-Z])| et vous| au sein| chez| dans l| position at|"
        r" position\b| at | n'est| est |$))",
        r"^(?:Test technique|Entretien|Invitation[^:–-]*)\s*[-:–]\s*(?P<p>.+?)(?:\s+chez\s+.*)?$",
        r"^Thales Careers - (?P<p>.+?)\s*\(",
        r"^Candidatures? via Indeed\s*:\s*(?P<p>.+)$",
        r"^Votre candidature\s*(?:-\s*offre\s+[\w-]+\s*[–-]\s*)?(?P<p>(?!sur|chez|pour)\S.+)$",
        r"^L'offre de (?P<p>.+?) n'est plus disponible",
    ]
    for motif in precis:
        for texte in (corps, objet) if motif.startswith("examin") or motif.startswith("r[ée]ponse") else (objet, corps):
            m = re.search(motif, texte, flags=re.I)
            if m:
                poste = _poste_propre(m.group("p"))
                if len(poste) >= 4 and not re.match(r"(?i)(chez|au sein|at)\b", poste) and not re.fullmatch(r"[\W\d_]+", poste):
                    return poste[:100]
    return ""


# ---------------------------------------------------------------------------
# Informations pour l'analyse des refus
# ---------------------------------------------------------------------------
CANAUX = [
    (r"hellowork", "HelloWork"), (r"indeed", "Indeed"), (r"welcomekit|welcometothejungle", "Welcome to the Jungle"),
    (r"linkedin", "LinkedIn"), (r"apec", "APEC"), (r"jobteaser", "JobTeaser"), (r"francetravail|pole-emploi", "France Travail"),
]


def canal(mail: Mail) -> str:
    """Plateforme par laquelle la candidature est passée (déduite de l'expéditeur)."""
    adresse = mail.expediteur_adresse.lower()
    for motif, nom in CANAUX:
        if re.search(motif, adresse):
            return nom
    return "Site carrière"


MOTIFS_REFUS = [
    ("Poste pourvu ou clôturé", r"pourvu|clos|close|cloture|plus disponible|no longer|has been filled|ne sera donc pas etudiee"),
    ("Autres candidats", r"autres? (candidats|profils)|other candidates|profils? plus (proche|adapte|en adequation)|"
                         r"plus en adequation|move forward with other|etape suivante de son processus"),
    ("Profil ne correspond pas", r"ne correspond(ant)? pas|criteres|competences|experience requise|attentes du poste|"
                                 r"not (a )?match|qualifications|requirements|profil recherche"),
]


def motif_refus(mail: Mail, classe: str) -> str:
    if classe == "cloture":
        return "Poste pourvu ou clôturé"
    t = mail.texte
    for nom, motif in MOTIFS_REFUS:
        if re.search(motif, t):
            return nom
    return "Sans motif précis"


# ---------------------------------------------------------------------------
# Rapprochement avec les lignes du Sheet
# ---------------------------------------------------------------------------
VIDES = {"groupe", "group", "sas", "sa", "france", "the", "et", "de", "du", "la", "le", "les", "des", "and", "inc", "ltd"}


def _mots(texte: str, mini: int = 3) -> set[str]:
    return {m for m in re.findall(r"[a-z0-9]+", norm(texte)) if len(m) >= mini and m not in VIDES}


def _entreprise_presente(entreprise: str, texte_n: str) -> bool:
    nom = norm(entreprise)
    if not nom:
        return False
    if re.search(rf"(?<![a-z0-9]){re.escape(nom)}(?![a-z0-9])", texte_n):
        return True
    mots = sorted(_mots(entreprise, 4), key=len, reverse=True)
    if mots and re.search(rf"(?<![a-z0-9]){re.escape(mots[0])}(?![a-z0-9])", texte_n):
        return True
    # Écritures différentes du même nom : « Eleclerc » / « E.Leclerc », « SaintGobain » / « Saint-Gobain ».
    compact = re.sub(r"[^a-z0-9]", "", nom)
    return len(compact) >= 5 and compact in re.sub(r"[^a-z0-9]", "", texte_n)


# Mots trop courants dans les intitulés pour distinguer deux postes.
MOTS_POSTE_GENERIQUES = {
    "stage", "stagiaire", "alternance", "alternant", "apprenti", "apprentie", "intern", "internship", "data",
    "analyst", "analyste", "junior", "mois", "cdi", "cdd", "poste", "offre", "assistant", "assistante",
}


def _postes_compatibles(poste_ligne: str, poste_mail: str) -> tuple[bool, float]:
    """Deux intitulés désignent-ils le même poste ? Sans intitulé exploitable d'un côté, on ne peut pas trancher."""
    a = _mots(poste_ligne) - MOTS_POSTE_GENERIQUES
    b = _mots(poste_mail) - MOTS_POSTE_GENERIQUES
    if not a or not b:
        return True, 0.0
    commun = len(a & b)
    score = max(commun / len(a), commun / len(b))
    return score >= 0.3, score


def trouver_ligne(df, mail: Mail, entreprise: str, poste: str):
    """Renvoie le numéro de ligne Sheet (_row) de la candidature concernée, ou None."""
    if df is None or df.empty:
        return None
    contexte = norm(f"{mail.expediteur_nom} {mail.expediteur_adresse} {mail.objet} {mail.corps[:3000]} {entreprise}")
    candidats = []
    for _, ligne in df.iterrows():
        ent = str(ligne.get("Entreprise", ""))
        if not ent.strip() or not (_entreprise_presente(ent, contexte) or (entreprise and norm(ent) == norm(entreprise))):
            continue
        ok, score = _postes_compatibles(str(ligne.get("Poste", "")), poste)
        if not ok:  # même entreprise, autre poste : ce n'est pas cette candidature
            continue
        active = str(ligne.get("Statut", "")) not in {"Refus", "Abandon", "Offre reçue"}
        candidats.append((score, active, str(ligne.get("Date", "")), int(ligne["_row"])))
    if not candidats:
        return None
    candidats.sort(reverse=True)
    # Plusieurs candidatures possibles et aucun intitulé pour les départager : on ne devine pas.
    if len(candidats) > 1 and candidats[0][0] == 0 and candidats[1][0] == 0:
        return None
    return candidats[0][3]


# ---------------------------------------------------------------------------
# Mise à jour des statuts
# ---------------------------------------------------------------------------
def nouveau_statut(classe: str, actuel: str) -> str | None:
    if actuel == "Offre reçue" and classe != "offre":
        return None
    if classe in {"refus", "cloture"}:
        return "Refus"
    if classe == "test":
        return "Test Technique"
    if classe == "entretien":
        return "Entretien Manager" if actuel in {"Entretien RH", "Test Technique", "Entretien Manager"} else "Entretien RH"
    if classe == "offre":
        return "Offre reçue"
    return None  # confirmation, CV consulté : pas de changement


@dataclass
class Rapport:
    lus: int = 0
    mis_a_jour: int = 0
    contacts: int = 0
    non_rattaches: list = field(default_factory=list)  # réponses sans candidature saisie : (date, type, entreprise, poste)
    details: list = field(default_factory=list)

    def resume(self) -> str:
        morceaux = []
        if self.mis_a_jour:
            morceaux.append(f"{self.mis_a_jour} candidature{'s' if self.mis_a_jour > 1 else ''} mise{'s' if self.mis_a_jour > 1 else ''} à jour")
        if self.contacts:
            morceaux.append(f"{self.contacts} contact{'s' if self.contacts > 1 else ''} ajouté{'s' if self.contacts > 1 else ''}")
        if self.non_rattaches:
            n = len(self.non_rattaches)
            morceaux.append(f"{n} réponse{'s' if n > 1 else ''} sans candidature saisie")
        return (", ".join(morceaux) + ".") if morceaux else "Aucune nouvelle réponse."


REPONSES = {"refus", "cloture", "test", "entretien", "offre"}
ADRESSE_AUTOMATIQUE = re.compile(
    r"no.?reply|ne.?pas.?repondre|nepasrepondre|donotreply|do-not-reply|notification|automate|send-only|"
    r"mailer|bounce|alert|newsletter|info@|contact@|candidature\.|@message\.|indeedapply|emails\.hellowork", re.I)


def contact_joignable(mail: Mail) -> tuple[str, str] | None:
    """Une adresse à laquelle on peut vraiment répondre (pas un robot), et le nom de la personne si on le connaît."""
    adresses = [(mail.expediteur_nom, mail.expediteur_adresse)]
    adresses += [("", a) for a in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", mail.corps[:4000])]
    for nom, adresse in adresses:
        adresse = adresse.strip(".").lower()
        if not adresse or ADRESSE_AUTOMATIQUE.search(adresse) or adresse.endswith(("gmail.com", "hotmail.com", "outlook.com")):
            continue
        personne = nom if re.fullmatch(r"[A-ZÀ-Ý][\w'-]+(?:\s+[A-ZÀ-Ý][\w'-]+){1,2}", (nom or "").strip()) else ""
        return adresse, personne
    return None


def synchroniser(store, journal, boite, aujourd_hui: date | None = None) -> Rapport:
    """Met à jour les candidatures saisies à partir des réponses reçues. Ne crée jamais de candidature.

    - refus, offre clôturée, test, entretien, proposition : statut, date de réponse, motif, canal ;
    - accusé de réception : seulement le contact, s'il y a une vraie adresse pour relancer ;
    - réponse sans candidature saisie : notée à part et retentée aux lectures suivantes,
      pour être rattachée dès que la candidature est ajoutée à la main.
    """
    aujourd_hui = aujourd_hui or date.today()
    traites = journal.ids()
    en_attente = journal.non_rattaches()  # id -> date du mail
    derniere = journal.derniere_date()
    depuis = (derniere - timedelta(days=2)) if derniere else aujourd_hui - timedelta(days=JOURS_PREMIERE_SYNCHRO)
    limite = aujourd_hui - timedelta(days=JOURS_PREMIERE_SYNCHRO)
    anciens = [d for d in en_attente.values() if d and d >= limite]
    if anciens:
        depuis = min(depuis, min(anciens))
    rapport = Rapport()
    df = store.charger()
    journal_nouveau = []

    for mail in sorted(boite.mails(depuis), key=lambda m: m.date):
        if not mail.message_id or mail.message_id in traites:
            continue
        classe = classer(mail)
        if classe is None:
            continue
        rapport.lus += 1
        entreprise = extraire_entreprise(mail)
        poste = extraire_poste(mail)
        row = trouver_ligne(df, mail, entreprise, poste)

        if row is None:
            if classe in REPONSES:
                rapport.non_rattaches.append((mail.date.isoformat(), LIBELLES[classe], entreprise or "?", poste))
                if mail.message_id not in en_attente:
                    journal_nouveau.append([mail.message_id, mail.date.isoformat(), classe, entreprise, poste, "",
                                            aujourd_hui.isoformat()])
            continue

        ligne = df.loc[df["_row"] == row].iloc[0]
        actuel = str(ligne.get("Statut", ""))
        commentaires = str(ligne.get("Commentaires", "") or "")
        note = f"{mail.date.isoformat()} · Gmail : {LIBELLES[classe]}"
        maj = {}

        if classe in REPONSES:
            if note not in commentaires:
                maj["Commentaires"] = f"{commentaires} | {note}".strip(" |")
            if not str(ligne.get("Canal", "") or "").strip():
                maj["Canal"] = canal(mail)
            if not str(ligne.get("Date_Reponse", "") or "").strip():
                maj["Date_Reponse"] = mail.date.isoformat()
            statut = nouveau_statut(classe, actuel)
            if statut and statut != actuel:
                maj["Statut"] = statut
                if classe in {"refus", "cloture"}:
                    maj["Motif_Refus"] = motif_refus(mail, classe)
                if classe in {"test", "entretien"}:
                    maj["Date_Relance"] = (mail.date + timedelta(days=DELAI_RELANCE)).isoformat()
        elif classe == "confirmation":
            contact = contact_joignable(mail)
            if contact and not str(ligne.get("Contact_Email", "") or "").strip():
                maj["Contact_Email"] = contact[0]
                if contact[1] and not str(ligne.get("Contact_Nom", "") or "").strip():
                    maj["Contact_Nom"] = contact[1]
                maj["Commentaires"] = f"{commentaires} | {mail.date.isoformat()} · Gmail : contact trouvé dans l'accusé de réception".strip(" |")
                rapport.contacts += 1
        elif classe == "cv_consulte" and note not in commentaires:
            maj["Commentaires"] = f"{commentaires} | {note}".strip(" |")

        if maj:
            store.modifier(row, maj)
            df = store.charger()
            if "Statut" in maj:
                rapport.mis_a_jour += 1
                rapport.details.append((str(ligne.get("Entreprise", "")), str(ligne.get("Poste", "")), maj["Statut"]))
        journal_nouveau.append([mail.message_id, mail.date.isoformat(), classe, entreprise, poste, str(row), aujourd_hui.isoformat()])
        traites.add(mail.message_id)

    if journal_nouveau:
        journal.ajouter(journal_nouveau)
    return rapport


# ---------------------------------------------------------------------------
# Accès réels : Gmail (IMAP) et onglet journal du Sheet
# ---------------------------------------------------------------------------
def _decoder(valeur: str | None) -> str:
    try:
        return str(make_header(decode_header(valeur or "")))
    except Exception:
        return valeur or ""


def _corps(message) -> str:
    texte, html_brut = "", ""
    for partie in message.walk() if message.is_multipart() else [message]:
        if partie.get_content_maintype() == "multipart" or partie.get_filename():
            continue
        try:
            contenu = partie.get_payload(decode=True).decode(partie.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if partie.get_content_type() == "text/plain" and not texte:
            texte = contenu
        elif partie.get_content_type() == "text/html" and not html_brut:
            html_brut = contenu
    return _nettoyer(texte if texte.strip() else _html_vers_texte(html_brut))


class GmailImap:
    """Lit les mails reçus (tous dossiers, hors envoyés) sans les marquer comme lus."""

    REQUETE = ("-from:me -in:sent -in:drafts (candidature OR candidatures OR postule OR application "
               "OR applying OR entretien OR interview OR recrutement OR recruitment)")

    def __init__(self, adresse: str, mot_de_passe: str, hote: str = "imap.gmail.com"):
        self.adresse, self.mot_de_passe, self.hote = adresse, mot_de_passe.replace(" ", ""), hote

    def _dossier_tous(self, imap) -> str:
        _, dossiers = imap.list()
        for ligne in dossiers or []:
            ligne = ligne.decode(errors="replace")
            if "\\All" in ligne:
                return ligne.split(' "/" ')[-1].strip()
        return "INBOX"

    def mails(self, depuis: date) -> list[Mail]:
        resultats = []
        with imaplib.IMAP4_SSL(self.hote) as imap:
            imap.login(self.adresse, self.mot_de_passe)
            imap.select(self._dossier_tous(imap), readonly=True)
            critere = f'after:{depuis.strftime("%Y/%m/%d")} {self.REQUETE}'
            _, donnees = imap.uid("SEARCH", "X-GM-RAW", f'"{critere}"')
            uids = (donnees[0] or b"").split()
            for uid in uids[-400:]:
                _, contenu = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                if not contenu or not isinstance(contenu[0], tuple):
                    continue
                message = email.message_from_bytes(contenu[0][1])
                try:
                    jour = parsedate_to_datetime(message.get("Date")).date()
                except Exception:
                    jour = date.today()
                nom, adresse = parseaddr(_decoder(message.get("From")))
                resultats.append(Mail(
                    message_id=(message.get("Message-ID") or f"uid-{uid.decode()}").strip(),
                    date=jour, expediteur_nom=nom, expediteur_adresse=adresse,
                    objet=_decoder(message.get("Subject")), corps=_corps(message),
                ))
        return resultats


class JournalSheet:
    """Onglet « Mails_traites » : un mail par ligne, pour ne jamais traiter deux fois le même."""

    ENTETES = ["Message_ID", "Date_Mail", "Type", "Entreprise_lue", "Poste_lu", "Ligne", "Traite_le"]

    def __init__(self, classeur):
        try:
            self.ws = classeur.worksheet("Mails_traites")
        except Exception:
            self.ws = classeur.add_worksheet("Mails_traites", rows=1000, cols=len(self.ENTETES))
            self.ws.append_row(self.ENTETES)
        self._valeurs = None

    def _lignes(self):
        if self._valeurs is None:
            self._valeurs = self.ws.get_all_values()[1:]
        return self._valeurs

    def ids(self) -> set[str]:
        """Mails rattachés à une candidature : on ne les relit plus."""
        return {l[0] for l in self._lignes() if len(l) > 5 and l[5]}

    def non_rattaches(self) -> dict:
        """Réponses sans candidature saisie (colonne Ligne vide), encore à rattacher."""
        traites = self.ids()
        out = {}
        for l in self._lignes():
            if len(l) > 5 and not l[5] and l[0] not in traites:
                try:
                    out[l[0]] = datetime.strptime(l[1], "%Y-%m-%d").date()
                except ValueError:
                    out[l[0]] = None
        return out

    def liste_non_rattaches(self) -> list[dict]:
        traites = self.ids()
        return [dict(Date=l[1], Type=LIBELLES.get(l[2], l[2]), Entreprise=l[3], Poste=l[4])
                for l in self._lignes() if len(l) > 5 and not l[5] and l[0] not in traites]

    def derniere_date(self) -> date | None:
        dates = [l[1] for l in self._lignes() if len(l) > 1 and l[1]]
        return datetime.strptime(max(dates), "%Y-%m-%d").date() if dates else None

    def ajouter(self, lignes: list[list]) -> None:
        self.ws.append_rows(lignes, value_input_option="RAW")
        self._valeurs = None
