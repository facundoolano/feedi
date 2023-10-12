# coding: utf-8
"""
This module contains tasks that can be scheduled by huey and/or run as flask cli commands.
(The cli commands could eventually be moved to another module).
"""

import csv
import datetime
import tempfile
from functools import wraps

import click
import filelock
import flask
import opml
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

                # using a lock file to ensure a given task is not attempted to run in parallel
                # so we can have multiple app worker processes without spamming rss sources with redundant requests
                lock_path = f'{tempfile.gettempdir()}/{f.__name__}-{fargs}-{fkwargs}'.replace(' ', '-')
                lock = filelock.FileLock(lock_path)
                try:
                    with lock.acquire(blocking=False):
                        f(*args, **kwargs)
                        app.logger.info("FINISHED %s %s %s", f.__name__, fargs, fkwargs)
                except filelock.Timeout:
                    app.logger.info("SKIPPING locked task %s", lock_path)

        return decorator

    def composed_decorator(f):
        return huey_decorator(with_context(f))

    return composed_decorator


@feed_cli.command('sync')
@huey_task(crontab(minute=app.config['SYNC_FEEDS_CRON_MINUTES']))
def sync_all_feeds():
    feeds = db.session.execute(db.select(models.Feed.name)).all()

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
@huey_task(crontab(minute='0', hour=app.config['DELETE_OLD_CRON_HOURS']))
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


@feed_cli.command('load')
@click.argument("file")
def csv_load(file):
    "Load feeds from a local csv file."

    with open(file) as csv_file:
        for values in csv.reader(csv_file):

            cls = models.Feed.resolve(values[0])
            feed = cls.from_valuelist(*values)
            add_if_not_exists(feed)

    db.session.commit()


@feed_cli.command('dump')
@click.argument("file")
def csv_dump(file):
    "Dump feeds to a local csv file."

    with open(file, 'w') as csv_file:
        feed_writer = csv.writer(csv_file)
        for feed in db.session.execute(db.select(models.Feed)).scalars():
            feed_writer.writerow(feed.to_valuelist())
            app.logger.info('written %s', feed)


@feed_cli.command('load-opml')
@click.argument("file")
def opml_load(file):
    document = opml.OpmlDocument.load(file)

    for outline in document.outlines:
        if outline.outlines:
            # it's a folder
            folder = outline.text
            for feed in outline.outlines:
                add_if_not_exists(models.RssFeed(name=feed.title or feed.text,
                                                 url=feed.xml_url,
                                                 folder=folder))

        else:
            # it's a top-level feed
            add_if_not_exists(models.RssFeed(name=feed.title or feed.text,
                                             url=feed.xml_url))

    db.session.commit()


@feed_cli.command('dump-opml')
@click.argument("file")
def opml_dump(file):
    document = opml.OpmlDocument()
    folder_outlines = {}
    for feed in db.session.execute(db.select(models.RssFeed)).scalars():
        if feed.folder:
            # to represent folder structure we put the feed in nested outlines
            if not feed.folder in folder_outlines:
                folder_outlines[feed.folder] = document.add_outline(feed.folder)
            target = folder_outlines[feed.folder]
        else:
            # if feed doesn't have a folder, put it in the top level doc
            target = document

        target.add_rss(feed.name,
                       feed.url,
                       title=feed.name,
                       categories=[feed.folder] if feed.folder else [],
                       created=datetime.datetime.now())

    document.dump(file)


def add_if_not_exists(feed):
    query = db.select(db.exists(models.Feed).where(models.Feed.name == feed.name))
    if db.session.execute(query).scalar():
        app.logger.info('skipping already existent %s', feed.name)
        return

    feed.load_icon()
    db.session.add(feed)
    app.logger.info('added %s', feed)


app.cli.add_command(feed_cli)
