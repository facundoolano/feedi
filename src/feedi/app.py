import datetime
import os
import time

import favicon
import feedparser
import requests
from flask import Flask, render_template

import feedi.models as models
from feedi.database import db


def create_app():
    app = Flask(__name__)
    app.config['TEMPLATES_AUTO_RELOAD'] = True

    # TODO review and organize db related setup code
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///feedi.db"
    db.init_app(app)

    with app.app_context():
        db.create_all()

        # FIXME remove
        load_hardcoded_feeds(app)

    @app.teardown_appcontext
    def shutdown_session(exception=None):
        db.session.remove()

    @app.route("/")
    def hello_world():
        # TODO query entries
        return render_template('base.html', entries=app.config['feeds'])

    # FIXME move somewhere else
    # TODO unit test this
    @app.template_filter('humanize')
    def humanize_date_filter(struct_time):
        "Pretty print a time.struct_time."

        delta = datetime.datetime.utcnow() - to_datetime(struct_time)

        if delta < datetime.timedelta(seconds=60):
            return f"{delta.seconds}s"
        elif delta < datetime.timedelta(hours=1):
            return f"{delta.seconds // 60}m"
        elif delta < datetime.timedelta(days=1):
            return f"{delta.seconds // 60 // 60 }h"
        elif delta < datetime.timedelta(days=8):
            return f"{delta.days}d"
        elif delta < datetime.timedelta(days=365):
            return time.strftime("%b %d", struct_time)
        return time.strftime("%b %d, %Y", struct_time)

    return app


def to_datetime(struct_time):
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))


def load_hardcoded_feeds(app):
    """
    Temporary setup to get some feed data for protoype development.
    Will eventually be moved to a db.
    """
    GOODREADS_TOKEN = os.getenv("GOODREADS_TOKEN")
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

    FEEDS = {
        "Apuntes Inchequeables": "https://facundoolano.github.io/feed.xml",
        "@grumpygamer": "https://mastodon.gamedev.place/@grumpygamer.rss",
        "lobste.rs": "https://lobste.rs/rss",
        "Github": f"https://github.com/facundoolano.private.atom?token={GITHUB_TOKEN}",
        # "ambito.com": "https://www.ambito.com/rss/pages/home.xml",
        "Goodreads": f"https://www.goodreads.com/home/index_rss/19714153?key={GOODREADS_TOKEN}"
    }

    for feed_name, url in FEEDS.items():

        query = db.select(models.Feed).where(models.Feed.name == feed_name)
        db_feed = db.session.execute(query).first()
        if db_feed:
            db_feed = db_feed[0]

        if db_feed and db_feed.last_fetch and datetime.datetime.utcnow() - db_feed.last_fetch < datetime.timedelta(minutes=60):
            # already got recent stuff
            continue

        app.logger.info('fetching %s', feed_name)
        feed = feedparser.parse(url)

        if not db_feed:
            db_feed = models.Feed(name=feed_name, url=url, icon_url=detect_feed_icon(app, feed),
                                  parser_type='default')
            db.session.add(db_feed)
            app.logger.info('added %s', db_feed)

        if 'updated_parsed' in feed and db_feed.last_fetch and datetime.datetime.utcnow() - to_datetime(feed['updated_parsed']) < datetime.timedelta(minutes=60):
            continue

        app.logger.info('adding entries for %s', feed_name)
        for entry in feed['entries']:
            if 'link' not in entry or 'summary' not in entry:
                app.logger.warn("entry seems malformed %s", entry)
                continue

            # TODO use type specific parsers
            db.session.add(models.Entry(feed=db_feed,
                                        title=entry.get('title', '[no title]'),
                                        title_url=entry['link'],
                                        avatar_url=detect_entry_avatar(feed, entry),
                                        username=entry.get('author'),
                                        body=entry['summary'],
                                        remote_created=to_datetime(entry['published_parsed']),
                                        remote_updated=to_datetime(entry['updated_parsed'])))

        db_feed.last_fetch = datetime.datetime.utcnow()

    db.session.commit()


def detect_feed_icon(app, feed):
    # FIXME should consider a feed returned url instead of the favicon?

    favicons = favicon.get(feed['feed']['link'])
    app.logger.debug("icons: %s", favicons)
    # if multiple formats, assume the .ico is the canonical one if present
    favicons = [f for f in favicons if f.format == 'ico'] or favicons
    href = favicons[0].url

    app.logger.debug('feed icon is %s', href)
    return href


def detect_entry_avatar(feed, entry):
    # FIXME this is brittle, we need to explicitly tell for each source type or even known source,
    # how do we expect to find the avatar
    url = (entry.get('media_thumbnail', [{}])[0].get('url') or feed['feed'].get('image', {}).get('href') or feed['feed'].get('webfeeds_icon'))
    if url:
        if not requests.head(url).ok:
            url = None

    return url
