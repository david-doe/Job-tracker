"""Synchronisation Gmail -> Google Sheet hors de l'app (tâche planifiée GitHub Actions).

Variables d'environnement : GMAIL_ADDRESS, GMAIL_APP_PASSWORD, GCP_SERVICE_ACCOUNT (JSON du compte de service),
et en option SHEET_NAME (par défaut « Job_Tracker_Data »).

Le dépôt est public, donc ses journaux d'exécution aussi : on n'y écrit que des compteurs,
jamais un nom d'entreprise, un poste ou le contenu d'un mail.
"""

import json
import os
import sys


def main() -> int:
    adresse = os.environ.get("GMAIL_ADDRESS", "")
    mot_de_passe = os.environ.get("GMAIL_APP_PASSWORD", "")
    compte = os.environ.get("GCP_SERVICE_ACCOUNT", "")
    if not (adresse and mot_de_passe and compte):
        print("Secrets absents : synchronisation non configurée, rien à faire.")
        return 0

    import gspread
    from oauth2client.service_account import ServiceAccountCredentials

    from store import SheetStore
    from sync_gmail import GmailImap, JournalSheet, synchroniser

    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(json.loads(compte), scope)
        classeur = gspread.authorize(creds).open(os.environ.get("SHEET_NAME") or "Job_Tracker_Data")
        rapport = synchroniser(
            SheetStore(classeur.worksheet("Candidatures")),
            JournalSheet(classeur),
            GmailImap(adresse, mot_de_passe),
        )
    except Exception as e:  # message volontairement générique : les journaux sont publics
        print(f"Échec de la synchronisation ({type(e).__name__}).")
        return 1
    print(f"Réponses lues : {rapport.lus} · mises à jour : {rapport.mis_a_jour} · contacts : {rapport.contacts} · "
          f"sans candidature saisie : {len(rapport.non_rattaches)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
