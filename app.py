import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta, datetime

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="Mon Suivi de Candidatures", layout="wide")

# --- CONNEXION GOOGLE SHEETS ---
def get_data():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = st.secrets["gcp_service_account"]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    sheet = client.open("Job_Tracker_Data").worksheet("Candidatures")
    return sheet

try:
    sheet = get_data()
    data = sheet.get_all_records()
    df = pd.DataFrame(data)
except Exception as e:
    st.error(f"Erreur de connexion : {e}")
    st.stop()

# --- SIDEBAR : MENU D'ACTION ---
st.sidebar.header("🚀 Actions")
action = st.sidebar.radio("Que veux-tu faire ?", ["Ajouter une candidature", "Mettre à jour un statut"])

# LISTE DES STATUTS POSSIBLES
status_options = ["Candidature envoyée", "Entretien RH", "Test Technique", "Entretien Manager", "Offre reçue", "Refus", "Abandon"]

# --- CAS 1 : AJOUTER ---
if action == "Ajouter une candidature":
    st.sidebar.subheader("Nouvelle Entrée")
    with st.sidebar.form(key='add_job_form'):
        company = st.text_input("Entreprise")
        city = st.text_input("Ville / Localisation")
        location = st.text_input("Ville / Localisation")
        role = st.text_input("Poste")
        link = st.text_input("Lien")
        status = st.selectbox("Statut", status_options)
        
        today = date.today()
        relance_date = st.date_input("Date Relance", today + timedelta(days=7))
        comments = st.text_area("Commentaires")
        
        submit_button = st.form_submit_button(label='Enregistrer')

    if submit_button:
        if company and role:
            # Ordre des colonnes : Date, Entreprise, Poste, Lien_Offre, Statut, Date_Relance, Commentaires
            new_row = [str(today), company, city, role, link, status, str(relance_date), comments]
            st.sidebar.success(f"Candidature {company} ajoutée !")
            st.rerun()
        else:
            st.sidebar.warning("Nom de l'entreprise requis.")

# --- CAS 2 : MODIFIER ---
elif action == "Mettre à jour un statut":
    st.sidebar.subheader("Mise à jour")
    
    if df.empty:
        st.sidebar.info("Aucune candidature à modifier.")
    else:
        # Créer une liste de sélection unique (Entreprise - Poste)
        # On garde l'index pour retrouver la ligne facilement
        options = df.index.astype(str) + " - " + df["Entreprise"] + " (" + df["Poste"] + ")"
        selected_option = st.sidebar.selectbox("Choisir la candidature", options)
        
        # Récupérer l'index sélectionné (le premier chiffre de la chaine "0 - Google...")
        selected_index = int(selected_option.split(" - ")[0])
        
        # Récupérer les données actuelles de la ligne sélectionnée
        current_status = df.at[selected_index, "Statut"]
        current_relance = df.at[selected_index, "Date_Relance"]
        current_comments = df.at[selected_index, "Commentaires"]
        
        # Formulaire de modification
        with st.sidebar.form(key='update_job_form'):
            # Trouver l'index du statut actuel dans la liste des options pour l'afficher par défaut
            try:
                status_index = status_options.index(current_status)
            except ValueError:
                status_index = 0
                
            new_status = st.selectbox("Nouveau Statut", status_options, index=status_index)
            
            # Gestion de la date (conversion string -> date object pour le widget)
            try:
                relance_dt = datetime.strptime(str(current_relance), "%Y-%m-%d").date()
            except:
                relance_dt = date.today()
                
            new_relance = st.date_input("Nouvelle Date Relance", relance_dt)
            new_comments = st.text_area("Mise à jour commentaires", current_comments)
            
            update_button = st.form_submit_button(label='Mettre à jour')
            
        if update_button:
            # Calcul du numéro de ligne dans Google Sheet
            # Index DataFrame commence à 0, Headers ligne 1 -> Donc Données commencent ligne 2
            # Row ID = Index + 2
            row_id = selected_index + 2
            
            # Mise à jour des cellules spécifiques (Col 5: Statut, Col 6: Relance, Col 7: Comm)
            sheet.update_cell(row_id, 5, new_status)
            sheet.update_cell(row_id, 6, str(new_relance))
            sheet.update_cell(row_id, 7, new_comments)
            
            st.sidebar.success("Mise à jour effectuée !")
            st.rerun()

# --- PAGE PRINCIPALE (Inchangée ou presque) ---
st.title("📊 Tableau de Bord Candidatures Data")

if not df.empty:
    # KPIs
    col1, col2, col3 = st.columns(3)
    col1.metric("Total", len(df))
    nb_entretiens = len(df[df['Statut'].str.contains("Entretien", case=False, na=False)])
    col2.metric("Entretiens", nb_entretiens)
    
    # Alertes Relance
   # 1. On convertit la colonne avec Pandas
    df['Date_Relance'] = pd.to_datetime(df['Date_Relance'], errors='coerce')
    
    # 2. On crée la date du jour avec Pandas aussi (le .normalize() met l'heure à minuit pile)
    today_timestamp = pd.to_datetime("today").normalize()
    
    # 3. On utilise 'today_timestamp' pour la comparaison
    to_contact = df[(df['Date_Relance'] <= today_timestamp) & (~df['Statut'].isin(['Refus', 'Abandon']))]

    st.markdown("---")
    
    # Tableaux
    if not to_contact.empty:
        st.error(f"⚠️ {len(to_contact)} Relances en retard ou pour aujourd'hui !")
        st.dataframe(to_contact[['Entreprise', 'Statut', 'Date_Relance', 'Commentaires']])

    st.subheader("Toutes les candidatures")
    st.dataframe(df, use_container_width=True)

else:
    st.info("Bienvenue ! Utilise le menu à gauche pour ajouter ta première candidature.")
