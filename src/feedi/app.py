# coding: utf-8

import datetime
import logging
import os
import urllib

import click
import flask
import lxml
from bs4 import BeautifulSoup
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

    # FIXME move somewhere else
    # TODO unit test this
    @app.template_filter('humanize')
    def humanize_date(dt):
        delta = datetime.datetime.utcnow() - dt

        if delta < datetime.timedelta(seconds=60):
            return f"{delta.seconds}s"
        elif delta < datetime.timedelta(hours=1):
            return f"{delta.seconds // 60}m"
        elif delta < datetime.timedelta(days=1):
            return f"{delta.seconds // 60 // 60 }h"
        elif delta < datetime.timedelta(days=8):
            return f"{delta.days}d"
        elif delta < datetime.timedelta(days=365):
            return dt.strftime("%b %d")
        return dt.strftime("%b %d, %Y")

    @app.template_filter('feed_domain')
    def feed_domain(feed):
        parts = urllib.parse.urlparse(feed.url or feed.server_url)
        return f'{parts.scheme}://{parts.netloc}'

    @app.template_filter('sanitize')
    def sanitize_content(html):
        # poor man's line truncating: reduce the amount of characters and let bs4 fix the html
        if len(html) > 500:
            html = html[:500] + '…'
            html = str(BeautifulSoup(html, 'lxml'))
        return html

    return app
