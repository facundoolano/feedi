import datetime
import logging
import time

from flask import Flask, render_template

import feedi.models as models
import feedi.parser as parser
from feedi.database import db


def create_app():
    app = Flask(__name__)

    # TODO manage via config
    app.logger.setLevel(logging.DEBUG)

    app.config['TEMPLATES_AUTO_RELOAD'] = True
    # TODO review and organize db related setup code
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///feedi.db"
    db.init_app(app)

    with app.app_context():
        db.create_all()

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    @app.route("/")
    def hello_world():
        q = db.select(models.Entry).order_by(models.Entry.remote_updated.desc())
        entries = db.paginate(q, per_page=100).items

        return render_template('base.html', entries=entries)

    @app.cli.command("feeds")
    def load_test_feeds():
        parser.load_test_feeds(app)

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
