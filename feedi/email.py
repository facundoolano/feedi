import smtplib
import urllib.parse
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart

import flask


def send(recipient, attach_data, filename):
    server = flask.current_app.config["FEEDI_EMAIL_SERVER"]
    port = flask.current_app.config["FEEDI_EMAIL_PORT"]
    sender = flask.current_app.config["FEEDI_EMAIL"]
    password = flask.current_app.config["FEEDI_EMAIL_PASSWORD"]

    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = f"feedi - {filename}"

    part = MIMEBase("application", "epub")
    part.set_payload(attach_data)
    encoders.encode_base64(part)

    # https://stackoverflow.com/a/216777/993769
    filename = urllib.parse.quote(filename)
    part.add_header("Content-Disposition", f"attachment; filename*=UTF-8''{filename}.epub")
    msg.attach(part)

    smtp = smtplib.SMTP(server, port)
    smtp.ehlo()
    smtp.starttls()
    smtp.login(sender, password)
    smtp.sendmail(sender, recipient, msg.as_string())
    smtp.quit()
