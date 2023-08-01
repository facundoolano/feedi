# coding: utf-8

import csv
import datetime
import json
import time

import click
# FIXME this shouldnt be here
import feedparser
import sqlalchemy.dialects.sqlite as sqlite
from flask import current_app as app

import feedi.models as models
import feedi.sources as sources
from feedi.models import db

# TODO parametrize in command or app config
RSS_SKIP_RECENTLY_UPDATED_MINUTES = 60
RSS_SKIP_OLDER_THAN_DAYS = 15


@app.cli.command("sync")
def sync_all_feeds():
    db_feeds = db.session.execute(db.select(models.Feed)).all()
    for (db_feed,) in db_feeds:
        if db_feed.type == models.Feed.TYPE_RSS:
            sync_rss_feed(app, db_feed)
        elif db_feed.type == models.Feed.TYPE_MASTODON_ACCOUNT:
            sync_mastodon_feed(app, db_feed)
        else:
            app.logger.error("unknown feed type %s", db_feed.type)
            continue

        db.session.commit()


def sync_mastodon_feed(app, db_feed):

    latest_entry = db_feed.entries.order_by(models.Entry.remote_updated.desc()).first()
    args = {}
    if latest_entry:
        # there's some entry on db, this is not the first time we're syncing
        # get all toots since the last seen one
        args['newer_than'] = latest_entry.remote_id
    else:
        # if there isn't any entry yet, get the "first page" of toots from the timeline
        # TODO make constant/config
        args['limit'] = 50

    app.logger.info("Fetching toots %s", args)
    toots = sources.mastodon.fetch_toots(app, server_url=db_feed.server_url,
                                         access_token=db_feed.access_token,
                                         **args)
    utcnow = datetime.datetime.utcnow()
    for values in toots:
        # upsert to handle already seen entries.
        # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
        values['updated'] = utcnow
        values['feed_id'] = db_feed.id
        db.session.execute(
            sqlite.insert(models.Entry).
            values(**values).
            on_conflict_do_update(("feed_id", "remote_id"), set_=values)
        )


def sync_rss_feed(app, db_feed):
    utcnow = datetime.datetime.utcnow()
    previous_fetch = db_feed.last_fetch

    if previous_fetch and utcnow - previous_fetch < datetime.timedelta(minutes=RSS_SKIP_RECENTLY_UPDATED_MINUTES):
        app.logger.info('skipping recently synced feed %s', db_feed.name)
        return

    app.logger.info('fetching %s', db_feed.name)
    feed = sources.rss.fetch(app, db_feed.urll, etag=db_feed.etag, modified=db_feed.modified_header)

    if not feed['feed']:
        app.logger.info('skipping empty feed %s %s', db_feed.name, feed.get('debug_message'))
        return

    db_feed.last_fetch = utcnow
    db_feed.etag = getattr(feed, 'etag', db_feed.etag)
    db_feed.modified_header = getattr(feed, 'modified', db_feed.modified_header)
    db_feed.raw_data = json.dumps(feed['feed'])

    # also checking with the internal updated field in case feed doesn't support the standard headers
    if previous_fetch and 'updated_parsed' in feed and to_datetime(feed['updated_parsed']) < previous_fetch:
        app.logger.info('skipping up to date feed %s', db_feed.name)
        return

    parser_cls = BaseParser
    # FIXME this is hacky, we aren't enforcing an order which may be necessary
    for cls in BaseParser.__subclasses__():
        if cls.is_compatible(db_feed.url, feed):
            parser_cls = cls
            break
    parser = parser_cls(feed, app.logger)

    app.logger.info('parsing %s with %s', parser_cls)
    for entry in feed['entries']:
        # again, don't try to process stuff that hasn't changed recently
        if previous_fetch and 'updated_parsed' in entry and to_datetime(entry['updated_parsed']) < previous_fetch:
            app.logger.debug('skipping up to date entry %s', entry['link'])
            continue

        # or that is too old
        if 'published_parsed' in entry and datetime.datetime.now() - to_datetime(entry['published_parsed']) > datetime.timedelta(days=RSS_SKIP_OLDER_THAN_DAYS):
            app.logger.debug('skipping old entry %s', entry['link'])
            continue

        try:
            values = parser.parse(entry)
        except Exception as e:
            app.logger.exception("parsing raised error: %s", e)
            continue

        # upsert to handle already seen entries.
        # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
        values['updated'] = utcnow
        values['feed_id'] = db_feed.id
        values['raw_data'] = json.dumps(entry)
        db.session.execute(
            sqlite.insert(models.Entry).
            values(**values).
            on_conflict_do_update(("feed_id", "remote_id"), set_=values)
        )


def to_datetime(struct_time):
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))

@app.cli.command("debug-feed")
@click.argument('url')
def debug_feed(url):
    import pprint

    feed = feedparser.parse(url)
    pp = pprint.PrettyPrinter(depth=10)
    pp.pprint(feed)


# TODO this should receive the file as arg
@app.cli.command("testfeeds")
def create_test_feeds():
    with open('feeds.csv') as csv_file:
        for attrs in csv.reader(csv_file):
            feed_type = attrs[0]
            feed_name = attrs[1]
            query = db.select(models.Feed).where(models.Feed.name == feed_name)
            db_feed = db.session.execute(query).first()
            if db_feed:
                app.logger.info('skipping already existent %s', feed_name)
                continue

            if feed_type == models.Feed.TYPE_RSS:
                url = attrs[2]
                feed = feedparser.parse(url)
                db_feed = models.RssFeed(name=feed_name,
                                         url=url,
                                         icon_url=sources.rss.detect_feed_icon(app, feed, url))

            elif feed_type == models.Feed.TYPE_MASTODON_ACCOUNT:
                server_url = attrs[2]
                access_token = attrs[3]

                db_feed = models.MastodonAccount(name=feed_name,
                                                 server_url=server_url,
                                                 access_token=access_token,
                                                 icon_url=sources.mastodon.fetch_avatar(server_url, access_token))

            else:
                app.logger.error("unknown feed type %s", attrs[0])
                continue

            db.session.add(db_feed)
            app.logger.info('added %s', db_feed)

    db.session.commit()


@app.cli.command("delete-feed")
@click.argument('feed-name')
def delete_feed(feed_name):
    query = db.delete(models.Feed).where(models.Feed.name == feed_name)
    db.session.execute(query)
    db.session.commit()
