"""Sources de la veille : API France Travail et Adzuna, alertes reçues dans Gmail.

Chaque source renvoie une liste d'Offre. Une source en panne ne bloque jamais les autres :
l'appelant attrape l'exception et passe à la suivante.

Les alertes mail ne sont gardées que si la date de publication est vérifiable :
- Indeed écrit l'âge de l'offre dans l'alerte (« Publié il y a 3 jours ») ;
- LinkedIn indique dans ses liens la date à partir de laquelle les offres ont été publiées ;
- HelloWork : la date « Publiée le … » est lue sur la page de l'offre ;
- les alertes des sites carrière (jobs2web / SuccessFactors) sont envoyées à la publication.
Les autres alertes (Glassdoor, Jooble, stage.fr…) renvoient souvent d'anciennes offres : elles sont ignorées,
leurs offres arrivent de toute façon par les API.
"""

from __future__ import annotations

import base64
import email
import html
import imaplib
import re
import time
from datetime import date, datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from veille import Offre, date_iso, date_relative, norm

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36", "Accept-Language": "fr-FR,fr;q=0.9"}

REQUETES = ["stage data analyst", "stage data scientist", "stage business intelligence",
            "stage analyste données", "stage chargé d'études statistiques", "stage power bi",
            "stage data", "stagiaire data"]


def _sans_html(texte: str) -> str:
    texte = re.sub(r"<br\s*/?>|</p>|</li>", "\n", texte or "", flags=re.I)
    texte = re.sub(r"<[^>]+>", " ", texte)
    return re.sub(r"[ \t]+", " ", html.unescape(texte)).strip()


# ---------------------------------------------------------------------------
# France Travail (API Offres d'emploi v2)
# ---------------------------------------------------------------------------
class FranceTravail:
    nom = "France Travail"
    URL_TOKEN = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=%2Fpartenaire"
    URL_RECHERCHE = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

    def __init__(self, client_id: str, client_secret: str, session=None):
        import requests
        self.client_id, self.client_secret = client_id, client_secret
        self.http = session or requests.Session()

    def _token(self) -> str:
        def demander(ident, secret):
            return self.http.post(self.URL_TOKEN, timeout=30, data={
                "grant_type": "client_credentials", "client_id": ident,
                "client_secret": secret, "scope": "api_offresdemploiv2 o2dsoffre"})
        r = demander(self.client_id, self.client_secret)
        if r.status_code in (400, 401):            # identifiant et clé secrète intervertis ?
            r2 = demander(self.client_secret, self.client_id)
            if r2.ok:
                r = r2
        r.raise_for_status()
        return r.json()["access_token"]

    def offres(self, jours: int = 7) -> list[Offre]:
        entetes = {"Authorization": f"Bearer {self._token()}", "Accept": "application/json"}
        publiee = min((p for p in (1, 3, 7, 14, 31) if p >= jours), default=31)
        resultats = []
        for requete in REQUETES:
            r = self.http.get(self.URL_RECHERCHE, headers=entetes, timeout=30,
                              params={"motsCles": requete, "publieeDepuis": publiee, "range": "0-149"})
            if r.status_code == 204:          # aucun résultat
                continue
            if r.status_code == 429:          # limite de débit : on patiente une fois
                time.sleep(2)
                r = self.http.get(self.URL_RECHERCHE, headers=entetes, timeout=30,
                                  params={"motsCles": requete, "publieeDepuis": publiee, "range": "0-149"})
            r.raise_for_status()
            for o in r.json().get("resultats", []):
                url = (o.get("origineOffre") or {}).get("urlOrigine") or \
                    f"https://candidat.francetravail.fr/offres/recherche/detail/{o.get('id')}"
                resultats.append(Offre(
                    source=self.nom, id_source=str(o.get("id", "")), titre=o.get("intitule", ""),
                    entreprise=(o.get("entreprise") or {}).get("nom", "") or "",
                    lieu=(o.get("lieuTravail") or {}).get("libelle", ""), url=url,
                    date_publication=date_iso(o.get("dateCreation", "")),
                    contrat=o.get("typeContratLibelle", "") or o.get("typeContrat", ""),
                    description=o.get("description", "") or "",
                ))
            time.sleep(0.3)
        return resultats


# ---------------------------------------------------------------------------
# Adzuna
# ---------------------------------------------------------------------------
class Adzuna:
    nom = "Adzuna"
    URL = "https://api.adzuna.com/v1/api/jobs/fr/search/{page}"

    def __init__(self, app_id: str, app_key: str, session=None):
        import requests
        self.app_id, self.app_key = app_id, app_key
        self.http = session or requests.Session()

    def offres(self, jours: int = 7) -> list[Offre]:
        resultats = []
        for requete in REQUETES:
            for page in (1, 2):
                r = self.http.get(self.URL.format(page=page), timeout=30, params={
                    "app_id": self.app_id, "app_key": self.app_key, "results_per_page": 50,
                    "what": requete, "max_days_old": jours, "sort_by": "date",
                    "content-type": "application/json"})
                r.raise_for_status()
                lignes = r.json().get("results", [])
                for o in lignes:
                    resultats.append(Offre(
                        source=self.nom, id_source=str(o.get("id", "")), titre=_sans_html(o.get("title", "")),
                        entreprise=_sans_html((o.get("company") or {}).get("display_name", "")),
                        lieu=_sans_html((o.get("location") or {}).get("display_name", "")),
                        url=o.get("redirect_url", ""), date_publication=date_iso(o.get("created", "")),
                        contrat=" ".join(x for x in (o.get("contract_type") or "", o.get("contract_time") or "") if x),
                        description=_sans_html(o.get("description", "")),
                    ))
                if len(lignes) < 50:
                    break
                time.sleep(0.3)
        return resultats


# ---------------------------------------------------------------------------
# Alertes Gmail
# ---------------------------------------------------------------------------
EXPEDITEURS_ALERTES = ["jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com", "jobalert.indeed.com",
                       "alerte@emails.hellowork.com", "notification@emails.hellowork.com", "jobs2web.com"]


def _decoder(valeur) -> str:
    try:
        return str(make_header(decode_header(valeur or "")))
    except Exception:
        return valeur or ""


def _parties(message) -> tuple[str, str]:
    texte, page = "", ""
    for partie in message.walk() if message.is_multipart() else [message]:
        if partie.get_content_maintype() == "multipart" or partie.get_filename():
            continue
        try:
            contenu = partie.get_payload(decode=True).decode(partie.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if partie.get_content_type() == "text/plain" and not texte:
            texte = contenu
        elif partie.get_content_type() == "text/html" and not page:
            page = contenu
    return texte, page


def html_en_lignes(page: str) -> list[tuple[str, str]]:
    """Transforme un mail HTML en suite de (texte, lien) dans l'ordre de lecture."""
    from html.parser import HTMLParser

    class Lecteur(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.items, self.lien, self.ignore = [], "", 0

        def handle_starttag(self, tag, attrs):
            if tag in ("style", "script", "head"):
                self.ignore += 1
            if tag == "a":
                self.lien = dict(attrs).get("href", "") or ""
                self.items.append(("", self.lien))

        def handle_endtag(self, tag):
            if tag in ("style", "script", "head"):
                self.ignore = max(0, self.ignore - 1)
            if tag == "a":
                self.lien = ""

        def handle_data(self, data):
            t = " ".join(data.split())
            if t and not self.ignore:
                self.items.append((t, self.lien))

    lecteur = Lecteur()
    lecteur.feed(page or "")
    return lecteur.items


def url_cachee(url: str) -> str:
    """Retrouve l'adresse réelle derrière un lien de suivi (paramètre url=… ou segment en base64)."""
    try:
        qs = parse_qs(urlparse(url).query)
        for cle in ("url", "u", "dest", "redirect", "target"):
            if cle in qs and qs[cle][0].startswith("http"):
                return unquote(qs[cle][0])
    except Exception:
        pass
    for segment in urlparse(url).path.split("/"):
        if len(segment) < 24:
            continue
        try:
            brut = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)).decode("utf-8", errors="ignore")
        except Exception:
            continue
        m = re.search(r"https?://[^\s\"'<>]+", brut)
        if m:
            return m.group(0)
    return url


class AlertesGmail:
    """Lit les alertes reçues ces derniers jours (sans les marquer comme lues)."""

    nom = "Alertes"

    def __init__(self, adresse: str, mot_de_passe: str, verifier_page=None, hote: str = "imap.gmail.com"):
        self.adresse, self.mot_de_passe, self.hote = adresse, mot_de_passe.replace(" ", ""), hote
        self.verifier_page = verifier_page          # fonction url -> date | None (HelloWork)

    def messages(self, jours: int):
        with imaplib.IMAP4_SSL(self.hote) as imap:
            imap.login(self.adresse.strip(), self.mot_de_passe.replace(" ", "").strip())
            _, dossiers = imap.list()
            tous = next((l.decode(errors="replace").split(' "/" ')[-1].strip()
                         for l in dossiers or [] if b"\\All" in l), "INBOX")
            imap.select(tous, readonly=True)
            depuis = " OR ".join(f"from:{e}" for e in EXPEDITEURS_ALERTES)
            _, donnees = imap.uid("SEARCH", "X-GM-RAW", f'"newer_than:{jours}d ({depuis})"')
            for uid in (donnees[0] or b"").split()[-300:]:
                _, contenu = imap.uid("FETCH", uid, "(BODY.PEEK[])")
                if contenu and isinstance(contenu[0], tuple):
                    yield email.message_from_bytes(contenu[0][1])

    def offres(self, jours: int = 7) -> list[Offre]:
        resultats = []
        for message in self.messages(min(jours, 7)):
            resultats.extend(lire_alerte(message, self.verifier_page))
        return resultats


def lire_alerte(message, verifier_page=None) -> list[Offre]:
    nom_expediteur, adresse_brute = parseaddr(_decoder(message.get("From")))
    adresse = adresse_brute
    try:
        recu = parsedate_to_datetime(message.get("Date")).date()
    except Exception:
        recu = date.today()
    texte, page = _parties(message)
    adresse = adresse.lower()
    if "linkedin.com" in adresse:
        return lire_linkedin(texte or "\n".join(t for t, _ in html_en_lignes(page)), recu)
    if "indeed.com" in adresse:
        return lire_indeed(texte, recu)
    if "hellowork.com" in adresse:
        return lire_hellowork(page, recu, verifier_page)
    if "jobs2web.com" in adresse:
        return lire_site_carriere(page, texte, nom_expediteur or adresse_brute, recu)
    return []


# --- LinkedIn --------------------------------------------------------------
BRUIT_LINKEDIN = re.compile(r"anciens? (eleves?|collegues?)|postulez|candidature simplifiee|recrute activement|"
                            r"^\d+ relations?|salaire|^offres? d.?emploi|^voir toutes|^modifier|^gerer|^promu|"
                            r"^nouvelles offres|^de nouvelles offres|^votre alerte", re.I)


def lire_linkedin(texte: str, recu: date) -> list[Offre]:
    """Bloc type : Titre / Entreprise / Lieu / … / « Voir l’offre d’emploi : …/jobs/view/123/ »."""
    lignes = [l.strip() for l in (texte or "").splitlines()]
    # Fenêtre de publication : f_TPR=a<horodatage>- dans les liens « Voir toutes les offres »
    fenetres = [int(x) for x in re.findall(r"f_TPR=a(\d{9,11})", texte or "")]
    debut = datetime.fromtimestamp(min(fenetres), tz=timezone.utc).date() if fenetres else None
    offres, tampon, vus = [], [], set()
    for ligne in lignes:
        m = re.search(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", ligne)
        if m:
            utiles = [l for l in tampon if l and not BRUIT_LINKEDIN.search(norm(l)) and "http" not in l
                      and not set(l) <= set("-—_ ")]
            if len(utiles) >= 2 and m.group(1) not in vus:
                vus.add(m.group(1))
                if len(utiles) >= 3:
                    titre, entreprise, lieu = utiles[-3:]
                else:
                    (titre, entreprise), lieu = utiles[-2:], ""
                offres.append(Offre(
                    source="LinkedIn", id_source=m.group(1), titre=titre, entreprise=entreprise, lieu=lieu,
                    url=f"https://www.linkedin.com/jobs/view/{m.group(1)}/",
                    # publiée entre « debut » et la réception : on retient la date la plus ancienne possible
                    date_publication=min(debut, recu) if debut else None,
                ))
            tampon = []
        elif set(ligne) <= set("-—_ ") and ligne:
            tampon = []
        else:
            tampon.append(ligne)
    return offres


# --- Indeed ----------------------------------------------------------------
def lire_indeed(texte: str, recu: date) -> list[Offre]:
    """Bloc type : Titre / Entreprise - Lieu / salaire / … / « Publié il y a 3 jours » / lien ?jk=…"""
    lignes = [l.strip() for l in (texte or "").splitlines()]
    offres, tampon, vus = [], [], set()
    for ligne in lignes:
        m = re.search(r"[?&]jk=([0-9a-f]{12,20})", ligne)
        if m and "indeed" in ligne:
            bloc = [l for l in tampon if l]
            date_pub = None
            for l in bloc:
                if re.search(r"publi|il y a|instant|aujourd|hier|ago|just posted", norm(l)):
                    date_pub = date_relative(l, recu)
            if len(bloc) >= 2 and m.group(1) not in vus:
                vus.add(m.group(1))
                entreprise, _, lieu = bloc[1].rpartition(" - ")
                if not entreprise:
                    entreprise, lieu = bloc[1], ""
                description = " ".join(l for l in bloc[2:] if not re.search(r"publi|candidature simplifiee", norm(l)))
                offres.append(Offre(
                    source="Indeed", id_source=m.group(1), titre=bloc[0], entreprise=entreprise.strip(),
                    lieu=lieu.strip(), url=f"https://fr.indeed.com/viewjob?jk={m.group(1)}",
                    date_publication=date_pub, description=description,
                ))
            tampon = []
        elif re.search(r"https?://", ligne):
            tampon = []
        else:
            tampon.append(ligne)
    return offres


# --- HelloWork -------------------------------------------------------------
def lire_hellowork(page: str, recu: date, verifier_page=None) -> list[Offre]:
    items = html_en_lignes(page)
    offres, vus = [], set()
    for i, (txt, lien) in enumerate(items):
        if not txt or not lien:
            continue
        reel = url_cachee(lien)
        m = re.search(r"hellowork\.com/fr-fr/emplois/(\d+)\.html", reel)
        if not m or m.group(1) in vus or norm(txt).startswith("voir"):
            continue
        vus.add(m.group(1))
        suite = [t for t, _ in items[i + 1:i + 14] if t and not norm(t).startswith("voir")]
        entreprise = suite[0] if suite else ""
        lieu = suite[1] if len(suite) > 1 else ""
        contrat = suite[2] if len(suite) > 2 else ""
        url = f"https://www.hellowork.com/fr-fr/emplois/{m.group(1)}.html"
        offres.append(Offre(source="HelloWork", id_source=m.group(1), titre=txt, entreprise=entreprise, lieu=lieu,
                            contrat=contrat, url=url,
                            date_publication=verifier_page(url) if verifier_page else None))
    return offres


def date_page_hellowork(url: str, session=None) -> date | None:
    """Lit « Publiée le jj/mm/aaaa » sur la page de l'offre HelloWork. None si l'offre n'existe plus."""
    import requests
    http = session or requests
    try:
        r = http.get(url, headers=UA, timeout=20)
    except Exception:
        return None
    if r.status_code != 200 or re.search(r"n.est plus disponible|offre expir", norm(r.text[:200000])):
        return None
    m = re.search(r"Publi[ée]e le (\d{1,2})/(\d{1,2})/(\d{4})", r.text)
    if m:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r'"datePosted"\s*:\s*"([^"]+)"', r.text)
    return date_iso(m.group(1)) if m else None


# --- Sites carrière (jobs2web / SuccessFactors) -----------------------------
def lire_site_carriere(page: str, texte: str, expediteur: str, recu: date) -> list[Offre]:
    """Alerte envoyée par le site carrière d'une entreprise au moment où l'offre est publiée."""
    entreprise = re.sub(r"(?i)[-_ ]?job ?notification.*$", "", expediteur.split("@")[0]).strip() or expediteur
    paires = [(t, l) for t, l in html_en_lignes(page) if t and "/job/" in l]
    if not paires:
        paires = re.findall(r"\[([^\]]{6,160})\]\((https?://[^)\s]+/job/[^)\s]+)\)", texte or "")
    offres, vus = [], set()
    for titre, url in paires:
        url = url.split("?")[0]
        if url in vus:
            continue
        vus.add(url)
        lieu = titre.rsplit(" - ", 1)[-1] if " - " in titre else ""
        titre_seul = titre.rsplit(" - ", 1)[0] if lieu else titre
        offres.append(Offre(source="Site carrière", titre=titre_seul, entreprise=entreprise, lieu=lieu,
                            url=url, date_publication=recu))
    return offres
