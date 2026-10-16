"""Envoi des relances depuis l'adresse Gmail de l'utilisateur (SMTP + mot de passe d'application)."""

from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid


@dataclass
class ConfigSMTP:
    adresse: str
    mot_de_passe: str
    nom_expediteur: str = ""
    hote: str = "smtp.gmail.com"
    port: int = 465

    @property
    def complete(self) -> bool:
        return bool(self.adresse and self.mot_de_passe)


def envoyer(config: ConfigSMTP, destinataire: str, objet: str, corps: str) -> None:
    """Envoie un mail texte. Lève une exception si l'envoi échoue."""
    msg = EmailMessage()
    msg["From"] = formataddr((config.nom_expediteur, config.adresse)) if config.nom_expediteur else config.adresse
    msg["To"] = destinataire
    msg["Subject"] = objet
    msg["Message-ID"] = make_msgid(domain=config.adresse.split("@")[-1])
    msg.set_content(corps)

    contexte = ssl.create_default_context()
    with smtplib.SMTP_SSL(config.hote, config.port, context=contexte, timeout=30) as serveur:
        serveur.login(config.adresse, config.mot_de_passe.replace(" ", ""))
        serveur.send_message(msg)
