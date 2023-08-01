# coding: utf-8

import logging
import os

import click
import flask
from dotenv import load_dotenv

import feedi.parser as parser
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

    # TODO review and organize db related setup code
    db.init_app(app)

    with app.app_context():
        db.create_all()

        from . import routes

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    # TODO add with a function instead of force decorating

    @app.cli.command("sync")
    def sync_feeds():
        parser.sync_all_feeds(app)

    @app.cli.command("testfeeds")
    def create_test_feeds():
        parser.create_test_feeds(app)

    @app.cli.command("debug-feed")
    @click.argument('url')
    def debug_feed(url):
        parser.debug_feed(url)

    @app.cli.command("delete-feed")
    @click.argument('feed-name')
    def delete_feed(feed_name):
        parser.delete_feed(app, feed_name)

    return app
