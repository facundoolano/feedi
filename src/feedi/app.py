# coding: utf-8

import logging

import flask

from feedi.models import db


def create_app():
    app = flask.Flask(__package__)
    app.config.from_object('feedi.config')
    app.config.from_envvar('FEEDI_CONFIG', silent=True)

    app.logger.setLevel(logging.DEBUG)

    db.init_app(app)

    with app.app_context():
        db.create_all()

        from . import filters, routes, tasks

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    return app
