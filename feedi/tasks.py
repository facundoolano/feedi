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
import flask
import flufl.lock as locklib
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
user_cli = flask.cli.AppGroup('user')

app.cli.add_command(feed_cli)
app.cli.add_command(user_cli)

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
                fargs = ' '.join([str(arg) for arg in args])
                fkwargs = ' '.join([f'{k}={v}' for (k, v) in kwargs.items()])

                app.logger.info("STARTING %s %s %s", f.__name__, fargs, fkwargs)

                f(*args, **kwargs)
                app.logger.info("FINISHED %s %s %s", f.__name__, fargs, fkwargs)

        return decorator

    def composed_decorator(f):
        return huey_decorator(with_context(f))

    return composed_decorator


def locked_task(lifetime):
    """
    Wraps a task execution with a file lock acquisition to prevent concurrent execution of the same task.
    The `lifetime` is the expected runtime of the task, used to expire the lock if for some reason a previous
    run fails to release it.
    """

    def decorator(f):
        lock_path = f'{tempfile.gettempdir()}/{f.__name__}'
        task_lock = locklib.Lock(lock_path, default_timeout=0)
        task_lock.lifetime = lifetime

        def inner(*args, **kwargs):

            app.logger.debug("locking %s", lock_path)

            try:
                with task_lock:
                    return f(*args, *kwargs)
            except (locklib.TimeOutError, locklib.AlreadyLockedError):
                app.logger.info("skipping locked task %s", lock_path)

        return inner

    return decorator


@feed_cli.command('sync')
@huey_task(crontab(minute=app.config['SYNC_FEEDS_CRON_MINUTES']))
@locked_task(lifetime=300)
def sync_all_feeds():
    feeds = db.session.execute(db.select(models.Feed.id, models.Feed.name)).all()

    tasks = []
    for feed in feeds:
        tasks.append((feed.name, sync_feed(feed.id, feed.name)))

    # wait for concurrent tasks to finish before returning
    for name, task in tasks:
        try:
            task.get()
        except:
            app.logger.exception("failure during async task %s", name)
            continue


@huey_task()
def sync_feed(feed_id, _feed_name, force=False):
    db_feed = db.session.get(models.Feed, feed_id)
    db_feed.sync_with_remote(force=force)
    db.session.commit()


@feed_cli.command('purge')
@huey_task(crontab(minute='0', hour=app.config['DELETE_OLD_CRON_HOURS']))
@locked_task(lifetime=20)
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


def load_user_arg(_ctx, _param, email):
    """
    CLI argument callback to load a user. If a user email is not provided explitly,
    fallback to the DEFAULT_AUTH_USER from the settings, otherwise raise an error.
    """
    if not email:
        email = app.config.get('DEFAULT_AUTH_USER')
        if not email:
            raise click.UsageError('No user provided and no DEFAULT_AUTH_USER set')

    user = db.session.scalar(db.select(models.User).filter_by(email=email))
    if not user:
        raise click.UsageError(f'User {email} not found')
    return user


@feed_cli.command('load')
@click.argument("file")
@click.argument('user', required=False, callback=load_user_arg)
def csv_load(file, user):
    "Load feeds from a local csv file."

    with open(file) as csv_file:
        for values in csv.reader(csv_file):

            cls = models.Feed.resolve(values[0])
            feed = cls.from_valuelist(*values)
            feed.user_id = user.id
            add_if_not_exists(feed)

    db.session.commit()


@feed_cli.command('dump')
@click.argument("file")
@click.argument('user', required=False, callback=load_user_arg)
def csv_dump(file, user):
    "Dump feeds to a local csv file."

    with open(file, 'w') as csv_file:
        feed_writer = csv.writer(csv_file)
        for feed in db.session.execute(db.select(models.Feed)
                                       .filter_by(user_id=user.id)).scalars():
            feed_writer.writerow(feed.to_valuelist())
            app.logger.info('written %s', feed)


@feed_cli.command('load-opml')
@click.argument("file")
@click.argument('user', required=False, callback=load_user_arg)
def opml_load(file, user):
    document = opml.OpmlDocument.load(file)

    for outline in document.outlines:
        if outline.outlines:
            # it's a folder
            folder = outline.text
            for feed in outline.outlines:
                add_if_not_exists(models.RssFeed(name=feed.title or feed.text,
                                                 user_id=user.id,
                                                 url=feed.xml_url,
                                                 folder=folder))

        else:
            # it's a top-level feed
            add_if_not_exists(models.RssFeed(name=feed.title or feed.text,
                                             user_id=user.id,
                                             url=feed.xml_url))

    db.session.commit()


@feed_cli.command('dump-opml')
@click.argument("file")
@click.argument('user', required=False, callback=load_user_arg)
def opml_dump(file, user):
    document = opml.OpmlDocument()
    folder_outlines = {}
    for feed in db.session.execute(db.select(models.RssFeed)
                                   .filter_by(user_id=user.id)
                                   ).scalars():
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


@user_cli.command('add')
@click.argument('email')
@click.password_option()
def user_add(email, password):
    user = models.User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()


@user_cli.command('del')
@click.argument('email')
def user_delete(email):
    stmt = db.delete(models.User)\
        .where(models.User.email == email)
    db.session.execute(stmt)
    db.session.commit()
