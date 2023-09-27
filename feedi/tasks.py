# coding: utf-8
"""
This module contains tasks that can be scheduled by huey and/or run as flask cli commands.
(The cli commands could eventually be moved to another module).
"""

import csv
import datetime
from functools import wraps

import click
import flask
import sqlalchemy as sa
from flask import current_app as app
from huey import crontab
from huey.contrib.mini import MiniHuey

import feedi.models as models
import feedi.parsers as parsers
from feedi.app import create_huey_app
from feedi.models import db

feed_cli = flask.cli.AppGroup('feed')

huey = MiniHuey()


def huey_task(*huey_args):
    "Wraps a function to make a it a MiniHuey task that is run inside a flask app context."

    huey_decorator = huey.task(*huey_args)

    def with_context(f):

        @wraps(f)
        def decorator(*args, **kwargs):
            # run the task inside an app context and log start and finish
            app = create_huey_app()
            with app.app_context():
                fargs = ' '.join(args)
                fkwargs = ' '.join([f'{k}={v}' for (k, v) in kwargs.items()])
                app.logger.info("STARTING %s %s %s", f.__name__, fargs, fkwargs)

                f(*args, **kwargs)

                app.logger.info("FINISHED %s %s %s", f.__name__, fargs, fkwargs)

        return decorator

    def composed_decorator(f):
        return huey_decorator(with_context(f))

    return composed_decorator


@feed_cli.command('sync')
@huey_task(crontab(minute=app.config['SYNC_FEEDS_CRON_MINUTES']))
def sync_all_feeds():
    feeds = db.session.execute(db.select(models.Feed.name, models.Feed.type)).all()

    tasks = []
    for feed in feeds:
        try:
            tasks.append(sync_feed(feed.name))
        except:
            app.logger.error("Skipping errored feed %s", feed.name)

    # wait for concurrent tasks to finish before returning
    for task in tasks:
        try:
            task.get()
        except:
            app.logger.exception("failure during async task %s", task)
            continue


@huey_task()
def sync_feed(feed_name):
    db_feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    db_feed.sync_with_remote()
    db.session.commit()


@feed_cli.command('purge')
@huey_task(crontab(minute=app.config['DELETE_OLD_CRON_HOURS']))
def delete_old_entries():
    """
    Delete entries that are older than DELETE_AFTER_DAYS but
    making sure we always keep RSS_MINIMUM_ENTRY_AMOUNT for each feed.
    Favorite and pinned entries aren't deleted.
    """
    older_than_date = (datetime.datetime.utcnow() -
                       datetime.timedelta(days=app.config['DELETE_AFTER_DAYS']))
    minimum = app.config['RSS_MINIMUM_ENTRY_AMOUNT']
    # there must be more clever sql ways to do this, but it doesn't have to be efficient

    # filter feeds that have old entries
    feeds_q = db.select(models.Feed.id, models.Feed.name)\
        .join(models.Feed.entries)\
        .filter(models.Entry.remote_updated < older_than_date,
                models.Entry.favorited.is_(None),
                models.Entry.pinned.is_(None)
                )\
        .group_by(models.Feed.id)\
        .having(sa.func.count(models.Feed.entries) > 0)

    for (feed_id, feed_name) in db.session.execute(feeds_q).all():
        # of the ones that have old entries, get the date of the nth entry (overall, not just within the old ones)
        min_remote_updated = db.session.scalar(
            db.select(models.Entry.remote_updated)
            .filter_by(feed_id=feed_id)
            .order_by(models.Entry.remote_updated.desc())
            .limit(1)
            .offset(minimum - 1))

        if not min_remote_updated:
            continue

        # delete all entries from that feed that are older than RSS_SKIP_OLDER_THAN_DAYS
        # AND ALSO older than the nth entry, so we guarantee to always keep at least the minimum
        q = db.delete(models.Entry)\
              .where(
                  models.Entry.favorited.is_(None),
                  models.Entry.pinned.is_(None),
                  models.Entry.feed_id == feed_id,
                  models.Entry.remote_updated < min_remote_updated,
                  models.Entry.remote_updated < older_than_date)

        res = db.session.execute(q)
        db.session.commit()
        if res.rowcount:
            app.logger.info("Deleted %s old entries from %s/%s", res.rowcount, feed_id, feed_name)


@feed_cli.command('debug')
@click.argument('url')
def debug_feed(url):
    parsers.rss.pretty_print(url)


# TODO this needs to be updated to new feed types and support folders
# we should also add csv exporting and opml import/export
@feed_cli.command('load')
@click.argument("file")
def create_test_feeds(file):
    "Load feeds from a local csv file."

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
                                         url=url)

            elif feed_type == models.Feed.TYPE_MASTODON_ACCOUNT:
                server_url = attrs[2]
                access_token = attrs[3]

                db_feed = models.MastodonAccount(name=feed_name,
                                                 url=server_url,
                                                 access_token=access_token)

            else:
                app.logger.error("unknown feed type %s", attrs[0])
                continue

            db_feed.load_icon()
            db.session.add(db_feed)
            app.logger.info('added %s', db_feed)

    db.session.commit()


app.cli.add_command(feed_cli)
