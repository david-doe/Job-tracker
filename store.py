"""Accès aux données : Google Sheet en production, données fictives en mode démo."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

COLONNES_BASE = ["Date", "Entreprise", "Ville", "Poste", "Lien_Offre", "Statut", "Date_Relance", "Commentaires"]
COLONNES_CONTACT = ["Contact_Nom", "Contact_Email", "Contact_LinkedIn", "Nb_Relances", "Derniere_Relance"]
# Remplies par la lecture Gmail, pour l'analyse des refus.
COLONNES_ANALYSE = ["Canal", "Date_Reponse", "Motif_Refus"]
COLONNES = COLONNES_BASE + COLONNES_CONTACT + COLONNES_ANALYSE


def _completer(df: pd.DataFrame) -> pd.DataFrame:
    for col in COLONNES:
        if col not in df.columns:
            df[col] = ""
    df["_row"] = range(2, len(df) + 2)  # ligne réelle dans le Sheet (en-tête = ligne 1)
    return df


class SheetStore:
    """Lecture et écriture dans l'onglet Candidatures, colonnes repérées par leur nom."""

    def __init__(self, worksheet):
        self.ws = worksheet

    def entetes(self) -> list[str]:
        return [h.strip() for h in self.ws.row_values(1)]

    def charger(self) -> pd.DataFrame:
        data = self.ws.get_all_records(expected_headers=[])
        return _completer(pd.DataFrame(data))

    def _assurer_colonnes(self, noms: list[str]) -> list[str]:
        entetes = self.entetes()
        manquantes = [n for n in noms if n not in entetes]
        if manquantes:
            besoin = len(entetes) + len(manquantes)
            if self.ws.col_count < besoin:
                self.ws.add_cols(besoin - self.ws.col_count)
            debut = len(entetes) + 1
            for i, nom in enumerate(manquantes):
                self.ws.update_cell(1, debut + i, nom)
            entetes = entetes + manquantes
        return entetes

    def ajouter(self, valeurs: dict) -> None:
        entetes = self._assurer_colonnes([k for k in valeurs if k in COLONNES])
        ligne = [str(valeurs.get(h, "")) for h in entetes]
        self.ws.append_row(ligne, value_input_option="RAW")

    def modifier(self, row: int, valeurs: dict) -> None:
        from gspread.utils import rowcol_to_a1

        entetes = self._assurer_colonnes(list(valeurs))
        maj = [
            {"range": rowcol_to_a1(row, entetes.index(nom) + 1), "values": [[str(val)]]}
            for nom, val in valeurs.items()
        ]
        self.ws.batch_update(maj, value_input_option="RAW")


class DemoStore:
    """Données fictives en mémoire, pour tester ou faire des captures sans données réelles."""

    def __init__(self):
        j = date.today()
        lignes = [
            [j - timedelta(days=12), "Shopify", "Ottawa", "Data Analyst", "https://www.linkedin.com/jobs/view/1", "Candidature envoyée", j - timedelta(days=2), "", "Sarah Chen", "", "https://www.linkedin.com/in/exemple", 0, ""],
            [j - timedelta(days=20), "Desjardins", "Montréal", "Analyste BI", "https://www.desjardins.com/carrieres", "Candidature envoyée", j - timedelta(days=1), "Relance 1 envoyée", "Julie Tremblay", "julie.tremblay@exemple.com", "", 1, j - timedelta(days=8)],
            [j - timedelta(days=9), "Ubisoft", "Montréal", "Data Scientist", "https://www.ubisoft.com/careers", "Entretien RH", j, "Entretien visio le 10/10", "Marc Lefebvre", "marc.lefebvre@exemple.com", "", 0, ""],
            [j - timedelta(days=15), "RBC", "Toronto", "Junior Data Scientist", "https://jobs.rbc.com", "Test Technique", j - timedelta(days=3), "Test SQL + Python rendu", "", "talent@exemple.com", "", 0, ""],
            [j - timedelta(days=6), "Lightspeed", "Montréal", "Data Analyst Produit", "https://www.lightspeedhq.com/careers", "Candidature envoyée", j + timedelta(days=1), "", "", "", "", 0, ""],
            [j - timedelta(days=30), "Coveo", "Québec", "Analyste de données", "https://www.coveo.com/fr/carrieres", "Refus", "", "Refus après entretien", "", "", "", 1, ""],
            [j - timedelta(days=25), "Element AI", "Montréal", "ML Analyst", "", "Entretien Manager", j + timedelta(days=4), "", "", "", "", 0, ""],
        ]
        self.df = pd.DataFrame([[str(v) for v in l] for l in lignes], columns=COLONNES_BASE + COLONNES_CONTACT)
        self.df["Canal"] = ["LinkedIn", "Site carrière", "Site carrière", "Site carrière", "Indeed", "Site carrière", "HelloWork"]
        self.df["Date_Reponse"] = ["", "", str(j - timedelta(days=4)), str(j - timedelta(days=10)), "", str(j - timedelta(days=18)), str(j - timedelta(days=21))]
        self.df["Motif_Refus"] = ["", "", "", "", "", "Autres candidats", ""]

    def charger(self) -> pd.DataFrame:
        return _completer(self.df.copy())

    def ajouter(self, valeurs: dict) -> None:
        ligne = {c: str(valeurs.get(c, "")) for c in COLONNES}
        self.df = pd.concat([self.df, pd.DataFrame([ligne])], ignore_index=True)

    def modifier(self, row: int, valeurs: dict) -> None:
        for nom, val in valeurs.items():
            self.df.at[row - 2, nom] = str(val)
