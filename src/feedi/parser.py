# coding: utf-8

import datetime
import os
import time

import favicon
import feedparser
import requests
import sqlalchemy.dialects.sqlite as sqlite

import feedi.models as models
from feedi.database import db

# TODO parametrize in command or app config
UPDATE_AFTER_MINUTES = 60


class BaseParser:
    """
    TODO
    """

    FIELDS = ['title', 'title_url', 'avatar_url', 'username', 'body',
              'remote_id', 'remote_created', 'remote_updated']

    @staticmethod
    def is_compatible(feed_url, feed_data):
        """
        Returns whether this class knows how to parse entries from the given feed.
        The base parser should reasonably work with any rss feed.
        """
        # FIXME this is confusing here
        return False

    # TODO review if this has a reasonable purpose vs just passing everything on the parse fun
    def __init__(self, feed, db_feed, logger):
        self.feed = feed
        self.db_feed = db_feed
        self.logger = logger

    def parse(self, entry):
        """
        TODO
        """
        if 'link' not in entry or 'summary' not in entry:
            raise ValueError(f"entry seems malformed {entry}")

        result = {}
        for field in self.FIELDS:
            method = 'parse_' + field
            result[field] = getattr(self, method)(entry)
        return result

    def parse_title(self, entry):
        return entry['title']

    def parse_title_url(self, entry):
        return entry['link']

    def parse_username(self, entry):
        return entry.get('author')

    def parse_avatar_url(self, entry):
        url = (self.feed['feed'].get('image', {}).get('href') or
               self.feed['feed'].get('webfeeds_icon'))

        if url and not requests.head(url).ok:
            url = None
        return url

    def parse_body(self, entry):
        return entry['summary']

    def parse_remote_id(self, entry):
        return entry['id']

    def parse_remote_created(self, entry):
        return to_datetime(entry['published_parsed'])

    def parse_remote_updated(self, entry):
        return to_datetime(entry['updated_parsed'])


class LinkAggregatorParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(_feed_url, feed_data):
        # TODO test this with lemmy as well
        KNOWN_AGGREGATORS = ['lobste.rs', 'reddit.com', 'news.ycombinator.com']
        return any([domain in feed_data['feed']['link'] for domain in KNOWN_AGGREGATORS])


class MastodonUserParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(_feed_url, feed_data):
        return 'mastodon' in feed_data['feed'].get('generator', '').lower()

    def parse_title(self, _entry):
        return self.feed['feed']['title']


class GithubFeedParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(feed_url, _feed_data):
        return 'github.com' in feed_url and 'private.atom' in feed_url


class GoodreadsFeedParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(feed_url, _feed_data):
        return 'goodreads.com' in feed_url and '/home/index_rss' in feed_url


def sync_all_feeds(app):
    db_feeds = db.session.execute(db.select(models.Feed)).all()
    for (db_feed,) in db_feeds:
        sync_feed(app, db_feed)

    db.session.commit()


def sync_feed(app, db_feed):
    if db_feed.last_fetch and datetime.datetime.utcnow() - db_feed.last_fetch < datetime.timedelta(minutes=UPDATE_AFTER_MINUTES):
        app.logger.info('skipping up to date feed %s', db_feed.name)
        return

    app.logger.info('fetching %s', db_feed.name)
    db_feed.last_fetch = datetime.datetime.utcnow()
    feed = feedparser.parse(db_feed.url)

    if 'updated_parsed' in feed and db_feed.last_fetch and datetime.datetime.utcnow() - to_datetime(feed['updated_parsed']) < datetime.timedelta(minutes=UPDATE_AFTER_MINUTES):
        app.logger.info('skipping up to date feed %s', db_feed.name)
        return

    parser_cls = BaseParser
    # FIXME this is hacky, we aren't enforcing an order which may be necessary
    for cls in BaseParser.__subclasses__():
        if cls.is_compatible(db_feed.url, feed):
            parser_cls = cls
            break
    parser = parser_cls(feed, db_feed, app.logger)

    app.logger.info('parsing %s with %s', db_feed.name, parser_cls)
    for entry in feed['entries']:
        try:
            values = parser.parse(entry)
        except Exception as e:
            app.logger.exception("parsing raised error: %s", e)
            continue

        # upsert to handle already seen entries.
        # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
        values['updated'] = db_feed.last_fetch
        values['feed_id'] = db_feed.id
        db.session.execute(
            sqlite.insert(models.Entry).
            values(**values).
            on_conflict_do_update(("feed_id", "remote_id"), set_=values)
        )


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


def debug_feed(url):
    feed = feedparser.parse(url)
    import pprint
    pp = pprint.PrettyPrinter(depth=10)
    pp.pprint(feed)


def create_test_feeds(app):
    GOODREADS_TOKEN = os.getenv("GOODREADS_TOKEN")
    GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

    FEEDS = {
        "Apuntes Inchequeables": "https://facundoolano.github.io/feed.xml",
        "@grumpygamer": "https://mastodon.gamedev.place/@grumpygamer.rss",
        "lobste.rs": "https://lobste.rs/rss",
        "Github": f"https://github.com/facundoolano.private.atom?token={GITHUB_TOKEN}",
        # "ambito.com": "https://www.ambito.com/rss/pages/home.xml",
        "Goodreads": f"https://www.goodreads.com/home/index_rss/19714153?key={GOODREADS_TOKEN}",
        "TheVerge": "https://www.theverge.com/rss/tech/index.xml",
        "ferd.ca": "https://ferd.ca/feed.rss",
        "r/programming": "https://www.reddit.com/r/programming/top.rss",
        "doctorow": "https://doctorow.medium.com/feed",
        "The New Yorker culture": "https://www.newyorker.com/feed/culture",
        "The New Yorker tech": "https://www.newyorker.com/feed/tech",
        "hackernews": "https://hnrss.org/newest?points=100",
        "DoubleFine": "https://www.doublefine.com/rss/news.rss",
        "mixnmojo": "https://www.theadventurer.news/feed",
        "Digital Antiquarian": "https://www.filfre.net/feed/rss/",
        "olé boke": "http://www.ole.com.ar/rss/boca-juniors/",
        "cinesargentinos": "http://feeds.feedburner.com/cinesargentinos-pelis",
        "arstechnica": "https://feeds.arstechnica.com/arstechnica/features.xml",
        "bytebytego": "https://blog.bytebytego.com/feed",
    }

    for feed_name, url in FEEDS.items():
        query = db.select(models.Feed).where(models.Feed.name == feed_name)
        db_feed = db.session.execute(query).first()
        if db_feed:
            app.logger.info('skipping already existent %s', feed_name)
            continue

        feed = feedparser.parse(url)
        db_feed = models.Feed(name=feed_name, url=url, icon_url=detect_feed_icon(app, feed))
        db.session.add(db_feed)
        app.logger.info('added %s', db_feed)

    db.session.commit()
