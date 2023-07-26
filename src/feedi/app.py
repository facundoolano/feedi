import datetime
import logging
import os

import click
from dotenv import load_dotenv
from flask import Flask, render_template, session

import feedi.models as models
import feedi.parser as parser
from feedi.database import db

# load environment variables from an .env file
load_dotenv()


def create_app():
    app = Flask(__name__)

    # TODO manage via config
    app.logger.setLevel(logging.DEBUG)

    app.secret_key = os.environ['FLASK_SECRET_KEY']

    app.config['TEMPLATES_AUTO_RELOAD'] = True
    # TODO review and organize db related setup code
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///feedi.db"
    db.init_app(app)

    with app.app_context():
        db.create_all()

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    # TODO move views to another module
    ENTRY_PAGE_SIZE = 20

    @app.route("/")
    def home():
        query = db.select(models.Entry).order_by(models.Entry.remote_updated.desc()).limit(ENTRY_PAGE_SIZE)
        entries = [e for (e, ) in db.session.execute(query)]
        return render_template('base.html', entries=entries)

    @app.route("/entries/after/<float:ts>/")
    def entry_page(ts):
        "Load a page of entries, older than the given timestamp. Used to implement infinite scrolling of the feed."
        dt = datetime.datetime.fromtimestamp(ts)
        query = db.select(models.Entry).filter(models.Entry.remote_updated < dt)\
                                       .order_by(models.Entry.remote_updated.desc()).limit(ENTRY_PAGE_SIZE)
        entries = [e for (e, ) in db.session.execute(query)]
        return render_template('entries.html', entries=entries)

    @app.route("/session/hide_media/", methods=['POST'])
    def toggle_hide_media():
        session['hide_media'] = not session.get('hide_media', False)
        return '', 204

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
    def humanize_date_filter(dt):

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
            # FIXME
            return dt.strftime("%b %d")
        # FIXME
        return dt.strftime("%b %d, %Y")

    return app
