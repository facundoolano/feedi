# coding: utf-8

import csv
import datetime
import json

import click
import flask
import sqlalchemy.dialects.sqlite as sqlite
from flask import current_app as app

import feedi.models as models
import feedi.sources as sources
from feedi.models import db

feed_cli = flask.cli.AppGroup('feed')

@feed_cli.command('sync')
def sync_all_feeds():
    db_feeds = db.session.execute(db.select(models.Feed)).all()
    for (db_feed,) in db_feeds:
        if db_feed.type == models.Feed.TYPE_RSS:
            sync_rss_feed(db_feed)
        elif db_feed.type == models.Feed.TYPE_MASTODON_ACCOUNT:
            sync_mastodon_feed(db_feed)
        else:
            app.logger.error("unknown feed type %s", db_feed.type)
            continue

        db.session.commit()


def sync_mastodon_feed(db_feed):

    latest_entry = db_feed.entries.order_by(models.Entry.remote_updated.desc()).first()
    args = {}
    if latest_entry:
        # there's some entry on db, this is not the first time we're syncing
        # get all toots since the last seen one
        args['newer_than'] = latest_entry.remote_id
    else:
        # if there isn't any entry yet, get the "first page" of toots from the timeline
        # TODO make constant/config
        args['limit'] = app.config['MASTODON_FETCH_LIMIT']

    app.logger.info("Fetching toots %s", args)
    toots = sources.mastodon.fetch_toots(server_url=db_feed.server_url,
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


def sync_rss_feed(db_feed):
    utcnow = datetime.datetime.utcnow()

    if db_feed.last_fetch and utcnow - db_feed.last_fetch < datetime.timedelta(minutes=app.config['RSS_SKIP_RECENTLY_UPDATED_MINUTES']):
        app.logger.info('skipping recently synced feed %s', db_feed.name)
        return

    app.logger.info('fetching %s', db_feed.name)
    entries, feed_data, etag, modified,  = sources.rss.fetch(db_feed.url,
                                                             db_feed.last_fetch,
                                                             app.config['RSS_SKIP_OLDER_THAN_DAYS'],
                                                             etag=db_feed.etag, modified=db_feed.modified_header)

    db_feed.last_fetch = utcnow
    db_feed.etag = etag
    db_feed.modified_header = modified
    db_feed.raw_data = json.dumps(feed_data)

    for entry_values in entries:
        # upsert to handle already seen entries.
        # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
        entry_values['updated'] = utcnow
        entry_values['feed_id'] = db_feed.id
        db.session.execute(
            sqlite.insert(models.Entry).
            values(**entry_values).
            on_conflict_do_update(("feed_id", "remote_id"), set_=entry_values)
        )

@feed_cli.command('debug')
@click.argument('url')
def debug_feed(url):
    sources.rss.pretty_print(url)


@feed_cli.command('load')
@click.argument("file")
def create_test_feeds(file):
    with open(file) as csv_file:
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
                db_feed = models.RssFeed(name=feed_name,
                                         url=url,
                                         icon_url=sources.rss.detect_feed_icon(url))

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


@feed_cli.command('delete')
@click.argument('feed-name')
def delete_feed(feed_name):
    query = db.delete(models.Feed).where(models.Feed.name == feed_name)
    db.session.execute(query)
    db.session.commit()


app.cli.add_command(feed_cli)
