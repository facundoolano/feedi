import datetime
import os
import time

import favicon
import feedparser
import requests
import sqlalchemy.dialects.sqlite as sqlite

import feedi.models as models
from feedi.database import db


# FIXME use generic logging instead of depending form app
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
            app.logger.info('skipping up to date feed %s', feed_name)
            continue

        app.logger.info('fetching %s', feed_name)
        last_fetch_time = datetime.datetime.utcnow()
        feed = feedparser.parse(url)

        if not db_feed:
            db_feed = models.Feed(name=feed_name, url=url, icon_url=detect_feed_icon(app, feed),
                                  parser_type='default')
            db.session.add(db_feed)
            app.logger.info('added %s', db_feed)

        if 'updated_parsed' in feed and db_feed.last_fetch and datetime.datetime.utcnow() - to_datetime(feed['updated_parsed']) < datetime.timedelta(minutes=60):
            app.logger.info('skipping up to date feed %s', feed_name)
            continue

        app.logger.info('adding entries for %s', feed_name)
        for entry in feed['entries']:
            if 'link' not in entry or 'summary' not in entry:
                app.logger.warn("entry seems malformed %s", entry)
                continue

            # TODO use type specific parsers here
            values = dict(feed_id=db_feed.id,
                          title=entry.get('title', '[no title]'),
                          title_url=entry['link'],
                          avatar_url=detect_entry_avatar(feed, entry),
                          username=entry.get('author'),
                          body=entry['summary'],
                          remote_id=entry['id'],
                          remote_created=to_datetime(entry['published_parsed']),
                          remote_updated=to_datetime(entry['updated_parsed']),
                          # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
                          updated=last_fetch_time)

            # upsert to handle already seen entries.
            db.session.execute(
                sqlite.insert(models.Entry).
                values(**values).
                on_conflict_do_update(("feed_id", "remote_id"), set_=values)
            )

        db_feed.last_fetch = last_fetch_time

    db.session.commit()


def to_datetime(struct_time):
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))


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
