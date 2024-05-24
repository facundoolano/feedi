import smtplib
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

import flask


def send(recipient, attach_data):
    server = flask.current_app.config['FEEDI_EMAIL_SERVER']
    port = flask.current_app.config['FEEDI_EMAIL_PORT']
    sender = flask.current_app.config['FEEDI_EMAIL']
    password = flask.current_app.config['FEEDI_EMAIL_PASSWORD']

    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = recipient
    msg['Subject'] = 'Feedi article submission'

    part = MIMEBase('application', 'octet-stream')
    part.set_payload(attach_data)
    encoders.encode_base64(part)
    part.add_header('Content-Disposition', f'attachment; filename=article.zip')
    msg.attach(part)

    with smtplib.SMTP_SSL(server, port) as smtp_server:
        smtp_server.login(sender, password)
        smtp_server.sendmail(sender, recipient, msg.as_string())
