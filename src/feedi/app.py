# coding: utf-8

import datetime
import logging
import os
import urllib

import click
import flask
import lxml
import newspaper
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

import feedi.models as models
import feedi.parser as parser
from feedi.database import db

# load environment variables from an .env file
load_dotenv()


def create_app():
    app = flask.Flask(__name__)

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
        return flask.render_template('base.html', entries=entries)

    @app.route("/entries/after/<float:ts>/")
    def entry_page(ts):
        "Load a page of entries, older than the given timestamp. Used to implement infinite scrolling of the feed."
        dt = datetime.datetime.fromtimestamp(ts)
        query = db.select(models.Entry).filter(models.Entry.remote_updated < dt)\
                                       .order_by(models.Entry.remote_updated.desc()).limit(ENTRY_PAGE_SIZE)
        entries = [e for (e, ) in db.session.execute(query)]
        return flask.render_template('entries.html', entries=entries)

    @app.route("/feeds/<int:id>/raw")
    def raw_feed(id):
        feed = db.get_or_404(models.Feed, id)

        return app.response_class(
            response=feed.raw_data,
            status=200,
            mimetype='application/json'
        )

    def error_fragment(msg):
        return flask.render_template("error_message.html", message=msg)

    @app.route("/entries/<int:id>/raw")
    def raw_entry(id):
        entry = db.get_or_404(models.Entry, id)
        return app.response_class(
            response=entry.raw_data,
            status=200,
            mimetype='application/json'
        )

    @app.route("/entries/<int:id>/content/", methods=['GET'])
    def fetch_entry_content(id):
        result = db.session.execute(db.select(models.Entry).filter_by(id=id)).first()
        if not result:
            return error_fragment("Entry not found")
        (entry, ) = result

        if entry.feed.type == models.Feed.TYPE_RSS:
            try:
                return extract_article(entry.content_url)
            except Exception as e:
                return error_fragment(f"Error fetching article: {repr(e)}")
        else:
            # this is not ideal for mastodon, but at least doesn't break
            return entry.body

    def extract_article(url):
        # TODO handle case if not html, eg if destination is a pdf
        # TODO to preserve the author data, maybe show the top image

        # https://stackoverflow.com/questions/62943152/shortcomings-of-newspaper3k-how-to-scrape-only-article-html-python
        config = newspaper.Config()
        config.fetch_images = True
        config.request_timeout = 30
        config.keep_article_html = True
        article = newspaper.Article(url, config=config)

        article.download()
        article.parse()

        # TODO unit test this
        # cleanup images from the article html
        soup = BeautifulSoup(article.article_html, 'lxml')
        for img in soup.find_all('img'):
            src = img.get('src')
            print(src, urllib.parse.urlparse(src).netloc)
            if not src:
                # skip images with missing src
                img.decompose()
            elif not urllib.parse.urlparse(src).netloc:
                # fix paths of relative img urls by joining with the main articule url
                img['src'] = urllib.parse.urljoin(url, src)

        return str(soup)

    @app.route("/session/hide_media/", methods=['POST'])
    def toggle_hide_media():
        flask.session['hide_media'] = not flask.session.get('hide_media', False)
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
