# Job Tracker

Suivi de candidatures en Streamlit, branché sur un Google Sheet, avec gestion des relances :
détection des relances dues, canal recommandé (email ou LinkedIn), mail rédigé selon l'étape
et la langue, validation, puis envoi depuis Gmail. Les réponses reçues dans Gmail (accusés de réception,
refus, tests, entretiens, propositions) mettent à jour automatiquement les candidatures saisies.

## Pages

- **Tableau de bord** : indicateurs, pipeline par statut, prochaines relances, historique filtrable.
- **Relances** : une carte par relance due, message modifiable, envoi en un clic ou via Gmail pré-rempli,
  report de 3 jours, clôture conseillée après 2 relances sans réponse. Chaque envoi met à jour le Sheet
  (nombre de relances, prochaine date, historique dans les notes).
- **Candidatures** : ajout et mise à jour, avec les coordonnées du contact.

## Lecture des réponses Gmail

L'app lit les réponses à tes candidatures et met à jour **les candidatures que tu as saisies toi-même**.
Elle ne crée jamais de candidature : la liste reste la tienne, ce qui évite de fausser l'analyse.

| Mail reçu | Effet sur la candidature correspondante |
|---|---|
| Refus, ou offre pourvue / clôturée | Statut « Refus », motif du refus, date de réponse |
| Invitation à un test (test technique, cas pratique, HackerRank…) | Statut « Test Technique », relance à J+7 |
| Invitation à un entretien | « Entretien RH », puis « Entretien Manager » pour le suivant |
| Proposition (promesse d'embauche, convention de stage…) | Statut « Offre reçue » |
| Accusé de réception | Seulement s'il vient d'une vraie personne ou contient une adresse joignable : ajout du contact pour les relances. Les accusés envoyés par un robot (« noreply ») sont ignorés |
| CV consulté | Note dans les commentaires |

Chaque changement ajoute une note datée dans `Commentaires`. Les alertes d'offres (LinkedIn, HelloWork, Indeed…) sont
ignorées, et une phrase au conditionnel (« si votre profil est retenu, un entretien sera organisé ») ne compte pas
comme une invitation.

Une réponse qui ne correspond à aucune candidature saisie n'est pas ajoutée : elle apparaît dans
**Analyse des refus → Réponses reçues sans candidature saisie**. Si tu ajoutes ensuite la candidature, la réponse
s'y rattache à la lecture suivante.

L'onglet `Mails_traites`, créé au premier passage, garde la trace des mails lus pour ne jamais traiter deux fois le même.
Au premier lancement, la lecture remonte 90 jours. Les mails ne sont pas marqués comme lus.

**Quand la lecture a lieu**

- Sur demande, avec le bouton « Lire les réponses Gmail » de la barre latérale (l'ouverture de l'app reste rapide).
  Elle utilise la section `[gmail]` des secrets (même mot de passe d'application que l'envoi). Pour la couper :
  `sync = false` dans `[gmail]`.
- En arrière-plan, toutes les heures de 8 h à minuit, grâce au workflow `.github/workflows/sync-gmail.yml`.
  Il faut trois secrets dans *Settings → Secrets and variables → Actions* du dépôt :
  `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` et `GCP_SERVICE_ACCOUNT` (le JSON complet du compte de service).
  Sans ces secrets, le workflow ne fait rien. Ses journaux ne contiennent que des compteurs, jamais d'entreprise
  ni de contenu de mail, car le dépôt est public.

## Analyse des refus

La page **Analyse des refus** sert à trouver ce qui bloque et à corriger les prochaines candidatures :

- indicateurs : candidatures avec une issue, avancées (entretien, test, offre), refus, postes déjà pourvus, délai médian de refus ;
- constats automatiques, chacun avec le nombre de candidatures sur lequel il repose et une action : refus très rapides
  (tri du CV), postes déjà pourvus (candidature trop tardive), groupe qui n'avance jamais, métiers trop dispersés,
  motifs de refus, comparaison avant / après le nouveau CV ;
- comparaison par type de contrat, métier, zone, canal, version du CV et mois, avec le détail des refus.

Le type de contrat, le métier et la zone sont déduits du poste et de la ville. `Canal`, `Date_Reponse` et `Motif_Refus`
sont remplis par la lecture Gmail. Un constat ne s'affiche que sur un groupe d'au moins 5 candidatures.
La date du nouveau CV se règle dans `analyse.py` (`DATE_NOUVEAU_CV`).

## Règles de relance

| Situation | Message | Relance suivante |
|---|---|---|
| Candidature envoyée, 1re relance | Confirmation de réception + intérêt | J+7 |
| Candidature envoyée, 2e relance | Dernière relance, demande de retour | J+10, puis clôture conseillée |
| Après entretien | Remerciement + prochaines étapes | J+7 |
| Après test technique | Demande de retour sur le test | J+7 |

Canal : email si l'email du contact est connu, sinon LinkedIn (profil du contact, ou recherche du recruteur).
Langue : anglais pour les villes anglophones (Toronto, Vancouver, Ottawa…), français sinon ; modifiable sur chaque carte.

## Secrets Streamlit

Dans Streamlit Cloud : *Manage app → Settings → Secrets*.

```toml
# Obligatoire : protège l'accès à l'app (elle est publique sans ça)
app_password = "un-mot-de-passe-solide"

[gcp_service_account]
# ... bloc existant du compte de service Google ...

# Envoi direct depuis Gmail (facultatif ; sans ça, bouton « Ouvrir dans Gmail »)
[gmail]
address = "ton.adresse@gmail.com"
app_password = "xxxx xxxx xxxx xxxx"   # mot de passe d'application Google, pas le mot de passe du compte
sender_name = "Prénom Nom"

[profil]
prenom = "Prénom"
signature = """Prénom Nom
Master SIAD – Data Science & Data Analysis
linkedin.com/in/ton-profil"""

# Facultatif : bouton « Améliorer avec l'IA »
[anthropic]
api_key = "sk-ant-..."
```

L'envoi direct n'est activé que si `app_password` est défini.

## Colonnes du Google Sheet

Colonnes d'origine : `Date, Entreprise, Ville, Poste, Lien_Offre, Statut, Date_Relance, Commentaires`.
L'app ajoute d'elle-même, au premier besoin : `Contact_Nom, Contact_Email, Contact_LinkedIn, Nb_Relances, Derniere_Relance`, puis `Canal, Date_Reponse, Motif_Refus`.

## Mode démo

```bash
JOBTRACKER_DEMO=1 streamlit run app.py
```

Données fictives, aucune connexion au Sheet : pratique pour tester ou faire des captures.
