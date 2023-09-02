# coding: utf-8

from gevent import monkey; monkey.patch_all()
import logging

import flask
from werkzeug.serving import is_running_from_reloader

import feedi.models as models


def create_app():
    app = flask.Flask(__package__)
    app.config.from_object('feedi.config.default')
    app.config.from_envvar('FEEDI_CONFIG', silent=True)

    app.logger.setLevel(logging.INFO)

    with app.app_context():
        from . import filters, routes, tasks

        models.init_db(app)

        if not is_running_from_reloader():
            # we want only one huey scheduler running, so we make sure
            # this isn't the dev server reloader process

            # btw this may not be the right place to put the huey startup
            # perhaps it should be in wsgi, but we wouldn't have it in dev server
            tasks.huey.start()

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        models.db.session.remove()

    return app


def create_huey_app():
    """
    Construct a minimal flask app for exposing to the huey tasks.
    This is necessary to make config and db session available to the periodic tasks.
    """
    app = flask.Flask('huey_app')
    app.config.from_object('feedi.config.default')
    app.config.from_envvar('FEEDI_CONFIG', silent=True)
    app.logger.setLevel(logging.INFO)
    with app.app_context():
        models.init_db(app)

    return app
