# -*- coding: utf-8 -*-
import os
import json

from smtplib import SMTP
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


with open(os.path.dirname(os.path.abspath(__file__)) + '/credentials.json', 'r') as file:
    credentials = json.loads(file.read())

def send_email(subject, text):
    e = credentials["email"]
    server = SMTP(e["smtp_server"], e["port"], "localhost")
    server.starttls()
    server.login(e["email-address"], e["email-password"])
    
    msg = MIMEMultipart()
    
    msg['From'] = "Odoo <office@luvifermente.eu>"
    msg['To'] = "viktor@fermentationculture.eu"
    msg['Subject'] = subject
    msg.attach(MIMEText(text, 'html'))

    server.send_message(msg)
    server.quit()