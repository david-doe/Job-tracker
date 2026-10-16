import hmac
import os
from datetime import date, timedelta

import pandas as pd
import streamlit as st

import relances as R
from mailer import ConfigSMTP, envoyer
from store import DemoStore, SheetStore

st.set_page_config(page_title="Suivi de candidatures", layout="wide", initial_sidebar_state="expanded")

MODE_DEMO = os.environ.get("JOBTRACKER_DEMO") == "1"
STATUTS = ["Candidature envoyée", "Entretien RH", "Test Technique", "Entretien Manager", "Offre reçue", "Refus", "Abandon"]


# ---------------------------------------------------------------------------
# Configuration et style
# ---------------------------------------------------------------------------
def secret(chemin: str, defaut=None):
    """Lit st.secrets["a"]["b"] à partir de "a.b" sans planter si la clé ou le fichier manque."""
    try:
        valeur = st.secrets
        for cle in chemin.split("."):
            valeur = valeur[cle]
        return valeur
    except Exception:
        return defaut


st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    html, body, [class*="css"], .stMarkdown, .stTextInput, .stSelectbox { font-family: 'Inter', sans-serif; }
    .block-container { padding-top: 2rem; max-width: 1200px; }
    h1 { font-weight: 700; letter-spacing: -0.02em; font-size: 1.9rem !important; }
    h2, h3 { font-weight: 600; letter-spacing: -0.01em; }
    [data-testid="stMetric"] {
        background: var(--secondary-background-color);
        border: 1px solid rgba(128,128,128,0.18);
        border-radius: 10px; padding: 14px 16px;
    }
    [data-testid="stMetricLabel"] p { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.04em; opacity: 0.75; }
    .meta { font-size: 0.85rem; opacity: 0.75; margin-top: -0.4rem; }
    .pill { display:inline-block; padding: 2px 10px; border-radius: 999px; font-size: 0.75rem; font-weight: 600;
            border: 1px solid rgba(128,128,128,0.3); margin-right: 6px; }
    footer { visibility: hidden; }
    </style>
    """,
    unsafe_allow_html=True,
)


# Couleurs qui suivent le thème clair / sombre choisi dans le menu de l'app.
try:
    SOMBRE = st.context.theme.type == "dark"
except Exception:
    SOMBRE = False
COULEURS = {
    "accent": "#7FB3E6" if SOMBRE else "#1F4E79",
    "email": ("#9CC7F0", "rgba(127,179,230,0.16)") if SOMBRE else ("#1F4E79", "rgba(31,78,121,0.12)"),
    "linkedin": ("#7DB8F5", "rgba(10,102,194,0.22)") if SOMBRE else ("#0A66C2", "rgba(10,102,194,0.10)"),
    "late": ("#F1948A", "rgba(231,76,60,0.18)") if SOMBRE else ("#B03A2E", "rgba(176,58,46,0.10)"),
}
# Palette sobre pour les indicateurs et les statuts (tons plus clairs en sombre).
TEINTES = {
    "bleu": "#7FB3E6" if SOMBRE else "#1F4E79",
    "sarcelle": "#5EC4B6" if SOMBRE else "#0F766E",
    "violet": "#B39DDB" if SOMBRE else "#5B3E96",
    "vert": "#7BC98F" if SOMBRE else "#2E7D4F",
    "ambre": "#E8B04B" if SOMBRE else "#B45F06",
    "rouge": "#F1948A" if SOMBRE else "#B03A2E",
    "gris": "#9AA5B1" if SOMBRE else "#5F6B7A",
}
COULEUR_STATUT = {
    "Candidature envoyée": "bleu", "Entretien RH": "violet", "Test Technique": "sarcelle",
    "Entretien Manager": "violet", "Offre reçue": "vert", "Refus": "rouge", "Abandon": "gris",
}
st.markdown(
    """
    <style>
    .kpi { border: 1px solid rgba(128,128,128,0.18); border-radius: 12px; padding: 14px 16px 12px;
           background: var(--secondary-background-color); height: 100%; }
    .kpi .lib { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; opacity: 0.75; font-weight: 600; }
    .kpi .val { font-size: 2rem; font-weight: 700; line-height: 1.2; color: var(--kpi-couleur); margin-top: 4px; }
    .kpi .sous { font-size: 0.78rem; opacity: 0.7; margin-top: 2px; }
    </style>
    """,
    unsafe_allow_html=True,
)


def kpi(colonne, libelle, valeur, teinte, sous=""):
    couleur = TEINTES[teinte]
    colonne.markdown(
        f"<div class='kpi' style='--kpi-couleur:{couleur}'>"
        f"<div class='lib'>{libelle}</div><div class='val'>{valeur}</div>"
        f"<div class='sous'>{sous}&nbsp;</div></div>",
        unsafe_allow_html=True,
    )


def style_statut(valeur):
    couleur = TEINTES.get(COULEUR_STATUT.get(valeur, ""), None)
    if not couleur:
        return ""
    return f"color: {couleur}; font-weight: 600"


st.markdown(
    "<style>"
    + "".join(
        f".pill-{nom} {{ color: {texte}; background: {fond}; border-color: {texte}55; }}"
        for nom, (texte, fond) in ((k, v) for k, v in COULEURS.items() if k != "accent")
    )
    + "</style>",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Accès protégé
# ---------------------------------------------------------------------------
def verifier_acces() -> bool:
    mot_de_passe = secret("app_password")
    if MODE_DEMO or not mot_de_passe:
        return True
    if st.session_state.get("acces_ok"):
        return True
    st.title("Suivi de candidatures")
    with st.form("connexion"):
        saisi = st.text_input("Mot de passe", type="password")
        ok = st.form_submit_button("Se connecter", type="primary")
    if ok:
        if hmac.compare_digest(saisi, str(mot_de_passe)):
            st.session_state.acces_ok = True
            st.rerun()
        else:
            st.error("Mot de passe incorrect.")
    st.stop()


verifier_acces()
APP_PROTEGEE = MODE_DEMO or bool(secret("app_password"))


# ---------------------------------------------------------------------------
# Données
# ---------------------------------------------------------------------------
@st.cache_resource
def get_classeur():
    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(dict(st.secrets["gcp_service_account"]), scope)
    return gspread.authorize(creds).open("Job_Tracker_Data")


@st.cache_resource
def get_store():
    if MODE_DEMO:
        return DemoStore()
    return SheetStore(get_classeur().worksheet("Candidatures"))


# Lecture des réponses Gmail : uniquement sur demande (bouton dans la barre latérale),
# pour que l'ouverture de l'app reste rapide.
GMAIL_LECTURE = (not MODE_DEMO and secret("gmail.address") and secret("gmail.app_password")
                 and secret("gmail.sync", True) is not False)


def synchroniser_gmail(store) -> None:
    from datetime import datetime

    from sync_gmail import GmailImap, JournalSheet, synchroniser

    try:
        with st.spinner("Lecture des réponses dans Gmail…"):
            rapport = synchroniser(
                store,
                JournalSheet(get_classeur()),
                GmailImap(secret("gmail.address"), secret("gmail.app_password")),
            )
        st.session_state.synchro = (datetime.now(), rapport.resume(), rapport.details)
    except Exception as e:
        st.session_state.synchro = (datetime.now(), f"Lecture Gmail impossible : {type(e).__name__}. "
                                    "Vérifie le mot de passe d'application et que l'IMAP est activé dans Gmail.", [])


try:
    store = get_store()
    if GMAIL_LECTURE and st.session_state.pop("synchro_demandee", False):
        synchroniser_gmail(store)
    df = store.charger()
except Exception as e:
    st.error(f"Connexion au Google Sheet impossible : {e}")
    st.stop()


def enregistrer(row: int, valeurs: dict, message: str):
    try:
        store.modifier(row, valeurs)
        st.session_state.flash = message
        st.rerun()
    except Exception as e:
        st.error(f"Échec de l'enregistrement : {e}")


SMTP = ConfigSMTP(
    adresse=secret("gmail.address", ""),
    mot_de_passe=secret("gmail.app_password", ""),
    nom_expediteur=secret("gmail.sender_name", ""),
)
SIGNATURE = secret("profil.signature", "David\nMaster SIAD – Data Science & Data Analysis")
PRENOM = secret("profil.prenom", "David")
ENVOI_DIRECT = SMTP.complete and APP_PROTEGEE

dues = R.relances_dues(df)

# ---------------------------------------------------------------------------
# Navigation
# ---------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### Suivi de candidatures")
    if MODE_DEMO:
        st.caption("Mode démo : données fictives")
    page = st.radio(
        "Navigation",
        ["Tableau de bord", f"Relances ({len(dues)})", "Candidatures", "Analyse des refus"],
        label_visibility="collapsed",
    )
    st.divider()
    st.caption("Apparence")
    st.markdown("Thème clair ou sombre : menu **⋮** en haut à droite → **Settings**.")
    if GMAIL_LECTURE:
        st.divider()
        st.caption("Réponses Gmail")
        quand, resume, details = st.session_state.get("synchro", (None, "", []))
        if quand:
            st.markdown(f"Dernière lecture à {quand:%H:%M} : {resume}")
        else:
            st.markdown("Lis tes mails pour mettre à jour les réponses reçues (refus, tests, entretiens).")
            for entreprise, poste, statut in details[:8]:
                st.caption(f"{entreprise} · {poste[:40]} → {statut}")
        if st.button("Lire les réponses Gmail", use_container_width=True):
            st.session_state.synchro_demandee = True
            st.rerun()
    st.divider()
    st.caption("Envoi des mails")
    if ENVOI_DIRECT:
        st.markdown(f"Envoi direct depuis **{SMTP.adresse}**")
    elif SMTP.complete and not APP_PROTEGEE:
        st.markdown("Envoi direct désactivé : ajoute `app_password` dans les secrets pour protéger l'app.")
    else:
        st.markdown("Envoi via Gmail pré-rempli. Ajoute la section `[gmail]` des secrets pour l'envoi en un clic.")

if msg := st.session_state.pop("flash", None):
    st.success(msg)


# ---------------------------------------------------------------------------
# Page : tableau de bord
# ---------------------------------------------------------------------------
def page_tableau_de_bord():
    st.title("Tableau de bord")
    if df.empty:
        st.info("Aucune candidature pour l'instant. Ajoute la première depuis l'onglet Candidatures.")
        return

    total = len(df)
    actives = (~df["Statut"].isin(R.STATUTS_CLOS)).sum()
    entretiens = df["Statut"].str.contains("Entretien|Test", case=False, na=False).sum()
    reponses = (~df["Statut"].isin(["Candidature envoyée", "Abandon", ""])).sum()
    taux = f"{reponses / total:.0%}" if total else "–"

    c1, c2, c3, c4, c5 = st.columns(5)
    kpi(c1, "Candidatures", total, "bleu", "au total")
    kpi(c2, "En cours", int(actives), "sarcelle", "processus actifs")
    kpi(c3, "Entretiens / tests", int(entretiens), "violet", "étapes avancées")
    kpi(c4, "Taux de réponse", taux, "vert", "réponses reçues")
    kpi(c5, "Relances dues", len(dues), "rouge" if len(dues) else "gris",
        "à traiter aujourd'hui" if len(dues) else "rien en attente")

    st.write("")
    gauche, droite = st.columns([1, 1.4])
    with gauche:
        st.subheader("Rythme de candidature")
        dates = pd.to_datetime(df["Date"], errors="coerce").dropna()
        lundi = pd.Timestamp(date.today()) - pd.Timedelta(days=date.today().weekday())
        semaines = pd.date_range(end=lundi, periods=8, freq="7D")
        par_semaine = (
            dates.dt.to_period("W-SUN").dt.start_time.value_counts()
            .reindex(semaines, fill_value=0)
        )
        cette_semaine = int(par_semaine.iloc[-1])
        moyenne = par_semaine.iloc[:-1].mean()
        par_semaine.index = par_semaine.index.strftime("%d/%m")
        st.bar_chart(par_semaine.rename("Candidatures"), color=COULEURS["accent"], height=250,
                     x_label="Semaine du", y_label="Candidatures", sort=False)
        st.caption(f"Cette semaine : {cette_semaine} candidature(s), "
                   f"moyenne des 7 semaines précédentes : {moyenne:.1f}.")
    with droite:
        st.subheader("Prochaines relances")
        a_venir = df[~df["Statut"].isin(R.STATUTS_CLOS)].copy()
        a_venir["Date_Relance"] = pd.to_datetime(a_venir["Date_Relance"], errors="coerce")
        a_venir = a_venir.dropna(subset=["Date_Relance"]).sort_values("Date_Relance").head(6)
        st.dataframe(
            a_venir[["Date_Relance", "Entreprise", "Poste", "Statut"]].style.map(style_statut, subset=["Statut"]),
            hide_index=True,
            width="stretch",
            column_config={"Date_Relance": st.column_config.DateColumn("Relance", format="DD/MM/YYYY")},
        )

    st.subheader("Historique")
    f1, f2 = st.columns([2, 1])
    recherche = f1.text_input("Rechercher", placeholder="Entreprise, poste, ville…", label_visibility="collapsed")
    filtre = f2.multiselect("Statut", STATUTS, placeholder="Tous les statuts", label_visibility="collapsed")
    vue = df.copy()
    if recherche:
        masque = vue[["Entreprise", "Poste", "Ville"]].astype(str).apply(
            lambda col: col.str.contains(recherche, case=False, na=False)
        ).any(axis=1)
        vue = vue[masque]
    if filtre:
        vue = vue[vue["Statut"].isin(filtre)]
    for col in ["Date", "Date_Relance"]:
        vue[col] = pd.to_datetime(vue[col], errors="coerce")
    st.dataframe(
        vue[["Date", "Entreprise", "Ville", "Poste", "Statut", "Lien_Offre", "Date_Relance", "Commentaires"]]
        .style.map(style_statut, subset=["Statut"]),
        hide_index=True,
        width="stretch",
        column_config={
            "Date": st.column_config.DateColumn("Candidature", format="DD/MM/YYYY"),
            "Date_Relance": st.column_config.DateColumn("Relance", format="DD/MM/YYYY"),
            "Lien_Offre": st.column_config.LinkColumn("Offre", display_text="Voir l'offre"),
        },
    )


# ---------------------------------------------------------------------------
# Page : relances
# ---------------------------------------------------------------------------
def marquer_envoyee(ligne, canal: str):
    numero = R.nb_relances(ligne) + 1
    enregistrer(
        int(ligne["_row"]),
        {
            "Nb_Relances": numero,
            "Derniere_Relance": date.today().isoformat(),
            "Date_Relance": R.prochaine_date(numero).isoformat(),
            "Commentaires": R.note_historique(ligne["Commentaires"], f"Relance {numero} envoyée ({canal})"),
        },
        f"Relance enregistrée pour {ligne['Entreprise']}.",
    )


def envoyer_un(ligne, destinataire, objet, corps) -> str | None:
    try:
        envoyer(SMTP, destinataire, objet, corps)
        return None
    except Exception as e:
        return str(e)


def personnaliser_ia(ligne, objet, corps, langue) -> str | None:
    cle = secret("anthropic.api_key")
    if not cle:
        return None
    import anthropic

    client = anthropic.Anthropic(api_key=cle)
    consigne = (
        "Améliore ce mail de relance de candidature pour qu'il soit naturel, concis et personnalisé. "
        "Garde la même langue, le même objectif et la signature. N'invente aucun fait. "
        "Utilise les notes si elles sont utiles. Réponds uniquement avec le corps du mail.\n\n"
        f"Entreprise : {ligne['Entreprise']}\nPoste : {ligne['Poste']}\nStatut : {ligne['Statut']}\n"
        f"Notes : {ligne['Commentaires']}\nLangue : {langue}\n\nObjet : {objet}\n\n{corps}"
    )
    rep = client.messages.create(
        model=secret("anthropic.model", "claude-sonnet-5-5"),
        max_tokens=800,
        messages=[{"role": "user", "content": consigne}],
    )
    return "".join(b.text for b in rep.content if getattr(b, "type", "") == "text").strip()


def initialiser(cle: str, defaut: str) -> str:
    """Prépare la valeur d'un champ éditable une seule fois, puis laisse l'utilisateur la modifier."""
    attente = st.session_state.pop(f"attente_{cle}", None)
    if attente is not None:
        st.session_state[cle] = attente
    elif cle not in st.session_state:
        st.session_state[cle] = defaut
    return cle


def page_relances():
    st.title("Relances")
    if dues.empty:
        st.success("Aucune relance à faire aujourd'hui.")
        return

    st.markdown(
        f"<p class='meta'>{len(dues)} relance(s) à traiter. Relis, ajuste si besoin, puis "
        + ("coche « Valider » et envoie tout en un clic.</p>" if ENVOI_DIRECT else "ouvre le mail dans Gmail et envoie.</p>"),
        unsafe_allow_html=True,
    )

    brouillons = []
    for idx, ligne in dues.iterrows():
        cle = int(ligne["_row"])
        reco = R.canal_recommande(ligne)
        numero = R.nb_relances(ligne) + 1
        retard = (pd.Timestamp(date.today()) - ligne["_date_relance"]).days

        with st.container(border=True):
            tete, actions = st.columns([3, 1])
            with tete:
                st.markdown(f"#### {ligne['Entreprise']} — {ligne['Poste']}")
                pastilles = (
                    f"<span class='pill pill-{reco.canal.lower()}'>{reco.canal} recommandé</span>"
                    f"<span class='pill'>{ligne['Statut']}</span>"
                    f"<span class='pill'>Relance n°{numero}</span>"
                )
                if numero > R.MAX_RELANCES:
                    pastilles += "<span class='pill pill-late'>Clôture conseillée</span>"
                if retard > 0:
                    pastilles += f"<span class='pill pill-late'>En retard de {retard} j</span>"
                st.markdown(pastilles, unsafe_allow_html=True)
                st.caption(reco.raison)

            c1, c2, c3 = st.columns([1, 1, 2])
            canal = c1.selectbox(
                "Canal", [R.CANAL_EMAIL, R.CANAL_LINKEDIN],
                index=0 if reco.canal == R.CANAL_EMAIL else 1, key=f"canal_{cle}",
            )
            langue = c2.selectbox(
                "Langue", ["FR", "EN"],
                index=0 if R.langue_suggeree(ligne["Ville"]) == "FR" else 1, key=f"langue_{cle}",
            )

            if canal == R.CANAL_EMAIL:
                dest = c3.text_input("Destinataire", key=initialiser(f"dest_{cle}", str(ligne["Contact_Email"] or "")),
                                     placeholder="recruteur@entreprise.com").strip()
                k_obj = initialiser(f"obj_{cle}_{langue}", R.objet_mail(ligne, langue))
                k_corps = initialiser(f"corps_{cle}_{langue}", R.corps_mail(ligne, langue, SIGNATURE))
                objet = st.text_input("Objet", key=k_obj)
                corps = st.text_area("Message", height=260, key=k_corps)

                b1, b2, b3, b4 = st.columns([1.2, 1, 1, 1])
                valide = False
                if ENVOI_DIRECT:
                    valide = b1.checkbox("Valider", key=f"ok_{cle}", disabled=not R.email_valide(dest))
                    if b2.button("Envoyer", key=f"send_{cle}", type="primary", disabled=not R.email_valide(dest)):
                        erreur = envoyer_un(ligne, dest, objet, corps)
                        if erreur:
                            st.error(f"Envoi impossible : {erreur}")
                        else:
                            if R.email_valide(dest) and dest != ligne["Contact_Email"]:
                                store.modifier(cle, {"Contact_Email": dest})
                            marquer_envoyee(ligne, "email")
                else:
                    b2.link_button("Ouvrir dans Gmail", R.lien_gmail(dest, objet, corps), width="stretch")
                    if b3.button("Marquer envoyée", key=f"mark_{cle}"):
                        if R.email_valide(dest) and dest != ligne["Contact_Email"]:
                            store.modifier(cle, {"Contact_Email": dest})
                        marquer_envoyee(ligne, "email")
                if secret("anthropic.api_key") and b4.button("Améliorer avec l'IA", key=f"iabtn_{cle}"):
                    with st.spinner("Rédaction en cours…"):
                        try:
                            nouveau = personnaliser_ia(ligne, objet, corps, langue)
                            if nouveau:
                                st.session_state[f"attente_{k_corps}"] = nouveau
                                st.rerun()
                        except Exception as e:
                            st.error(f"IA indisponible : {e}")
                if valide:
                    brouillons.append((ligne, dest, objet, corps))
            else:
                texte = R.message_linkedin(ligne, langue, PRENOM)
                message = st.text_area("Message LinkedIn (300 caractères max)", max_chars=300,
                                       height=120, key=initialiser(f"li_{cle}_{langue}", texte))
                st.code(message, language=None, wrap_lines=True)
                profil = str(ligne["Contact_LinkedIn"] or "")
                cible = profil if profil.startswith("http") else R.lien_recherche_linkedin(ligne["Entreprise"])
                b1, b2 = st.columns([1, 1])
                b1.link_button("Ouvrir LinkedIn" if profil else "Trouver le recruteur", cible, width="stretch")
                if b2.button("Marquer envoyée", key=f"mark_{cle}", width="stretch"):
                    marquer_envoyee(ligne, "LinkedIn")

            r1, r2, _ = st.columns([1, 1, 3])
            if r1.button("Reporter de 3 jours", key=f"later_{cle}"):
                enregistrer(cle, {"Date_Relance": (date.today() + timedelta(days=3)).isoformat()},
                            f"Relance {ligne['Entreprise']} reportée.")
            if R.nb_relances(ligne) >= R.MAX_RELANCES:
                if r2.button("Clore la candidature", key=f"close_{cle}"):
                    enregistrer(cle, {"Statut": "Abandon",
                                      "Commentaires": R.note_historique(ligne["Commentaires"], "Close après relances sans réponse")},
                                f"Candidature {ligne['Entreprise']} close.")

    if ENVOI_DIRECT:
        st.divider()
        if st.button(f"Envoyer les {len(brouillons)} mail(s) validé(s)", type="primary", disabled=not brouillons):
            erreurs = []
            for ligne, dest, objet, corps in brouillons:
                err = envoyer_un(ligne, dest, objet, corps)
                if err:
                    erreurs.append(f"{ligne['Entreprise']} : {err}")
                    continue
                numero = R.nb_relances(ligne) + 1
                maj = {
                    "Nb_Relances": numero,
                    "Derniere_Relance": date.today().isoformat(),
                    "Date_Relance": R.prochaine_date(numero).isoformat(),
                    "Commentaires": R.note_historique(ligne["Commentaires"], f"Relance {numero} envoyée (email)"),
                }
                if dest != ligne["Contact_Email"]:
                    maj["Contact_Email"] = dest
                store.modifier(int(ligne["_row"]), maj)
            if erreurs:
                st.error("Certains envois ont échoué :\n\n" + "\n\n".join(erreurs))
            else:
                st.session_state.flash = f"{len(brouillons)} relance(s) envoyée(s)."
                st.rerun()


# ---------------------------------------------------------------------------
# Page : candidatures (ajout et modification)
# ---------------------------------------------------------------------------
def page_candidatures():
    st.title("Candidatures")
    onglet_ajout, onglet_modif = st.tabs(["Nouvelle candidature", "Mettre à jour"])

    with onglet_ajout:
        with st.form("ajout", clear_on_submit=True):
            a, b = st.columns(2)
            entreprise = a.text_input("Entreprise *")
            poste = b.text_input("Poste *")
            ville = a.text_input("Ville")
            lien = b.text_input("Lien de l'offre")
            statut = a.selectbox("Statut", STATUTS)
            relance = b.date_input("Date de relance", date.today() + timedelta(days=7), format="DD/MM/YYYY")
            st.markdown("**Contact (facultatif, utile pour les relances)**")
            c, d, e = st.columns(3)
            contact_nom = c.text_input("Nom du contact")
            contact_email = d.text_input("Email du contact")
            contact_li = e.text_input("Profil LinkedIn du contact")
            commentaires = st.text_area("Notes")
            ok = st.form_submit_button("Enregistrer", type="primary")
        if ok:
            if not (entreprise and poste):
                st.warning("L'entreprise et le poste sont obligatoires.")
            elif contact_email and not R.email_valide(contact_email):
                st.warning("L'email du contact n'est pas valide.")
            else:
                try:
                    store.ajouter({
                        "Date": date.today().isoformat(), "Entreprise": entreprise, "Ville": ville, "Poste": poste,
                        "Lien_Offre": lien, "Statut": statut, "Date_Relance": relance.isoformat(),
                        "Commentaires": commentaires, "Contact_Nom": contact_nom, "Contact_Email": contact_email,
                        "Contact_LinkedIn": contact_li, "Nb_Relances": 0,
                    })
                    st.session_state.flash = f"Candidature chez {entreprise} ajoutée."
                    st.rerun()
                except Exception as e:
                    st.error(f"Échec de l'enregistrement : {e}")

    with onglet_modif:
        if df.empty:
            st.info("Aucune candidature à modifier.")
            return
        libelles = {
            int(r["_row"]): f"{r['Entreprise']} — {r['Poste']}" + (f" ({r['Ville']})" if r["Ville"] else "")
            for _, r in df.iterrows()
        }
        row = st.selectbox("Candidature", list(libelles), format_func=libelles.get)
        ligne = df[df["_row"] == row].iloc[0]
        try:
            relance_actuelle = pd.to_datetime(ligne["Date_Relance"]).date()
        except Exception:
            relance_actuelle = date.today()
        with st.form(f"modif_{row}"):
            a, b = st.columns(2)
            statut = a.selectbox("Statut", STATUTS,
                                 index=STATUTS.index(ligne["Statut"]) if ligne["Statut"] in STATUTS else 0)
            relance = b.date_input("Date de relance", relance_actuelle, format="DD/MM/YYYY")
            c, d, e = st.columns(3)
            contact_nom = c.text_input("Nom du contact", str(ligne["Contact_Nom"] or ""))
            contact_email = d.text_input("Email du contact", str(ligne["Contact_Email"] or ""))
            contact_li = e.text_input("Profil LinkedIn du contact", str(ligne["Contact_LinkedIn"] or ""))
            commentaires = st.text_area("Notes", str(ligne["Commentaires"] or ""), height=140)
            ok = st.form_submit_button("Mettre à jour", type="primary")
        if ok:
            if contact_email and not R.email_valide(contact_email):
                st.warning("L'email du contact n'est pas valide.")
            else:
                enregistrer(row, {
                    "Statut": statut, "Date_Relance": relance.isoformat(), "Commentaires": commentaires,
                    "Contact_Nom": contact_nom, "Contact_Email": contact_email, "Contact_LinkedIn": contact_li,
                }, "Candidature mise à jour.")


# ---------------------------------------------------------------------------
# Page : analyse des refus
# ---------------------------------------------------------------------------
def page_analyse():
    import analyse as A

    st.title("Analyse des refus")
    st.markdown("<div class='meta'>Où tes candidatures bloquent, et ce qui distingue celles qui avancent. "
                "Les constats ne s'affichent que sur des groupes d'au moins "
                f"{A.EFFECTIF_MIN} candidatures.</div>", unsafe_allow_html=True)
    if df.empty:
        st.info("Pas encore de candidatures à analyser.")
        return
    d = A.enrichir(df)
    base = d[d["Issue"] != "Abandon"]
    decidees = base[base["Issue"] != "En attente"]
    refus = decidees[decidees["Issue"].isin(["Refus", "Clôturée"])]
    delais = refus["Délai de réponse (j)"].dropna()

    c1, c2, c3, c4, c5 = st.columns(5)
    n = len(decidees)
    kpi(c1, "Avec une issue", n, "bleu", f"{len(base) - n} en attente")
    kpi(c2, "Avancées", int((decidees["Issue"] == "Avancée").sum()), "violet", "entretiens, tests, offres")
    kpi(c3, "Refus", int((decidees["Issue"] == "Refus").sum()), "rouge", "refus reçus")
    kpi(c4, "Postes pourvus", int((decidees["Issue"] == "Clôturée").sum()), "gris", "offres clôturées")
    kpi(c5, "Délai de refus", f"{int(delais.median())} j" if len(delais) else "–", "sarcelle",
        f"médiane sur {len(delais)} refus" if len(delais) else "pas encore de date de réponse")

    st.write("")
    st.subheader("Constats")
    liste = A.constats(d)
    if not liste:
        st.info("Pas encore assez de candidatures avec une issue pour tirer des constats fiables.")
    for c in liste:
        with st.container(border=True):
            st.markdown(f"**{c.titre}**")
            st.caption(c.detail)
            st.markdown(f"→ {c.action}")

    st.write("")
    st.subheader("Par groupe")
    dimension = st.radio("Comparer par", ["Type de contrat", "Métier", "Zone", "Canal", "Version du CV", "Mois"],
                         horizontal=True, label_visibility="collapsed", key="dimension_analyse")
    t = A.tableau(d, dimension)
    graphique = t[["Avancées", "Refus", "Clôturées", "Sans réponse", "En attente"]]
    st.bar_chart(graphique, horizontal=True, stack=True, height=60 + 38 * len(graphique))
    st.dataframe(
        t.style.format({"Taux d'avancée": lambda x: "–" if pd.isna(x) else f"{x:.0%}",
                        "Délai médian (j)": lambda x: "–" if pd.isna(x) else f"{x:.0f}"}),
        use_container_width=True,
    )

    if GMAIL_LECTURE:
        try:
            from sync_gmail import JournalSheet

            orphelines = JournalSheet(get_classeur()).liste_non_rattaches()
        except Exception:
            orphelines = []
        if orphelines:
            with st.expander(f"Réponses reçues sans candidature saisie ({len(orphelines)})"):
                st.caption("Ces réponses ne comptent pas dans l'analyse. Ajoute la candidature dans l'onglet "
                           "Candidatures si tu veux qu'elle compte : la réponse s'y rattachera à la prochaine lecture.")
                st.dataframe(pd.DataFrame(orphelines), use_container_width=True, hide_index=True)

    if not refus.empty:
        st.subheader("Motifs de refus")
        motifs = refus["Motif_Refus"].replace("", "Non lu (ajouté à la main)").value_counts()
        st.bar_chart(motifs, horizontal=True, height=60 + 38 * len(motifs))
        st.subheader("Détail des refus")
        st.dataframe(
            refus.sort_values("date_cand", ascending=False)[
                ["Date", "Entreprise", "Poste", "Type de contrat", "Métier", "Canal", "Motif_Refus", "Délai de réponse (j)"]
            ].rename(columns={"Motif_Refus": "Motif"}),
            use_container_width=True, hide_index=True,
        )


if page == "Tableau de bord":
    page_tableau_de_bord()
elif page.startswith("Relances"):
    page_relances()
elif page == "Analyse des refus":
    page_analyse()
else:
    page_candidatures()
