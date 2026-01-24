import streamlit as st
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import date, timedelta, datetime

# --- CONFIGURATION ---
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
    st.error(f"Erreur critique de connexion au Google Sheet : {e}")
    st.stop()

# --- SIDEBAR : ACTIONS ---
st.sidebar.header("🚀 Actions")
action = st.sidebar.radio("Menu", ["Ajouter une candidature", "Mettre à jour un statut"])

status_options = ["Candidature envoyée", "Entretien RH", "Test Technique", "Entretien Manager", "Offre reçue", "Refus", "Abandon"]

# --- ACTION 1 : AJOUTER ---
if action == "Ajouter une candidature":
    st.sidebar.subheader("Nouvelle Entrée")
    with st.sidebar.form(key='add_job_form'):
        company = st.text_input("Entreprise")
        city = st.text_input("Ville / Localisation")
        role = st.text_input("Poste")
        link = st.text_input("Lien")
        status = st.selectbox("Statut", status_options)
        
        today = date.today()
        relance_date = st.date_input("Date Relance", today + timedelta(days=7))
        comments = st.text_area("Commentaires")
        
        submit_button = st.form_submit_button(label='Enregistrer')

    if submit_button:
        if company and role:
            # Sécurité : On essaie d'envoyer à Google, si ça échoue on affiche l'erreur
            try:
                new_row = [str(today), company, city, role, link, status, str(relance_date), comments]
                sheet.append_row(new_row)
                st.sidebar.success(f"✅ Candidature chez {company} ajoutée avec succès !")
                st.rerun()
            except Exception as e:
                st.sidebar.error(f"Erreur lors de l'envoi à Google : {e}")
        else:
            st.sidebar.warning("⚠️ L'Entreprise et le Poste sont obligatoires.")

# --- ACTION 2 : MODIFIER ---
elif action == "Mettre à jour un statut":
    st.sidebar.subheader("Mise à jour")
    if df.empty:
        st.sidebar.info("Aucune donnée.")
    else:
        if "Ville" in df.columns:
            options = df.index.astype(str) + " - " + df["Entreprise"] + " [" + df["Ville"] + "] - " + df["Poste"]
        else:
            options = df.index.astype(str) + " - " + df["Entreprise"] + " - " + df["Poste"]

        selected_option = st.sidebar.selectbox("Choisir la ligne", options)
        selected_index = int(selected_option.split(" - ")[0])
        
        current_status = df.at[selected_index, "Statut"]
        current_relance = df.at[selected_index, "Date_Relance"]
        current_comments = df.at[selected_index, "Commentaires"]
        
        with st.sidebar.form(key='update_job_form'):
            try:
                status_index = status_options.index(current_status)
            except ValueError:
                status_index = 0
            new_status = st.selectbox("Nouveau Statut", status_options, index=status_index)
            
            try:
                relance_dt = datetime.strptime(str(current_relance), "%Y-%m-%d").date()
            except:
                relance_dt = date.today()
                
            new_relance = st.date_input("Nouvelle Date Relance", relance_dt)
            new_comments = st.text_area("Mise à jour commentaires", current_comments)
            update_button = st.form_submit_button(label='Mettre à jour')
            
        if update_button:
            row_id = selected_index + 2
            sheet.update_cell(row_id, 6, new_status)      
            sheet.update_cell(row_id, 7, str(new_relance))
            sheet.update_cell(row_id, 8, new_comments)    
            st.sidebar.success("✅ Mis à jour !")
            st.rerun()

# --- PAGE PRINCIPALE : TABLEAU DE BORD ---
st.title("📊 Tableau de Bord Data")

if not df.empty:
    col1, col2, col3 = st.columns(3)
    col1.metric("Candidatures", len(df))
    nb_entretiens = len(df[df['Statut'].str.contains("Entretien", case=False, na=False)])
    col2.metric("Entretiens", nb_entretiens)
    
    # Correction robuste pour les dates de relance
    df['Date_Relance'] = pd.to_datetime(df['Date_Relance'], errors='coerce')
    today_timestamp = pd.to_datetime("today").normalize()
    to_contact = df[(df['Date_Relance'] <= today_timestamp) & (~df['Statut'].isin(['Refus', 'Abandon']))]
    
    col3.metric("A relancer", len(to_contact), delta_color="inverse")
    st.markdown("---")
    
    if not to_contact.empty:
        st.error("🔥 Relances urgentes")
        cols_to_show = ['Entreprise', 'Ville', 'Statut', 'Date_Relance', 'Commentaires']
        cols_final = [c for c in cols_to_show if c in df.columns]
        st.dataframe(to_contact[cols_final])

    st.subheader("Historique Complet")
    desired_order = ['Date', 'Entreprise', 'Ville', 'Poste', 'Statut', 'Lien_Offre', 'Date_Relance', 'Commentaires']
    final_order = [c for c in desired_order if c in df.columns]
    st.dataframe(df[final_order], use_container_width=True)

else:
    st.info("Bienvenue ! Ajoute ta première candidature via le menu à gauche.")
