# coding: utf-8

from gevent import monkey

monkey.patch_all()  # nopep8
import logging
import os

import flask
from werkzeug.serving import is_running_from_reloader

import feedi.models as models


def create_app():
    app = flask.Flask(__package__)
    load_config(app)
    app.logger.info('Starting app with FLASK_ENV=%s', os.getenv('FLASK_ENV'))

    with app.app_context():
        from . import auth, filters, routes, tasks

        models.init_db(app)

        auth.init()

        if not is_running_from_reloader() and not os.environ.get('DISABLE_CRON_TASKS'):
            # we want only one huey scheduler running, so we make sure
            # this isn't the dev server reloader process

            # btw this may not be the right place to put the huey startup
            # perhaps it should be in wsgi, but we wouldn't have it in dev server
            app.logger.info("Starting Huey for periodic tasks")
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
    load_config(app)

    with app.app_context():
        models.init_db(app)

    return app


def load_config(app):
    app.logger.setLevel(logging.INFO)
    env = os.getenv('FLASK_ENV')
    if not env:
        app.logger.error('FLASK_ENV not set')
        exit()

    app.config.from_object('feedi.config.default')
    app.config.from_object(f'feedi.config.{env}')
