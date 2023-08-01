# coding: utf-8

import logging
import os

import flask
from dotenv import load_dotenv

from feedi.models import db

# load environment variables from an .env file
load_dotenv()


def create_app():
    app = flask.Flask(__name__)

    # TODO setup config file
    app.logger.setLevel(logging.DEBUG)
    app.secret_key = os.environ['FLASK_SECRET_KEY']
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///feedi.db"

    db.init_app(app)

    with app.app_context():
        db.create_all()

        from . import filters, routes, tasks

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    return app
