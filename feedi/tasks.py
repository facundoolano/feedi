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
import opml
import sqlalchemy as sa
from huey import crontab
from huey.contrib.mini import MiniHuey

import feedi.models as models
import feedi.parsers as parsers
from feedi.app import create_huey_app
from feedi.models import db

app = create_huey_app()
huey = MiniHuey(pool_size=app.config['HUEY_POOL_SIZE'])

feed_cli = flask.cli.AppGroup('feed')
user_cli = flask.cli.AppGroup('user')

flask.current_app.cli.add_command(feed_cli)
flask.current_app.cli.add_command(user_cli)


def huey_task(*huey_args):
    "Wraps a function to make a it a MiniHuey task that is run inside a flask app context."

    huey_decorator = huey.task(*huey_args)

    def with_context(f):

        @wraps(f)
        def decorator(*args, **kwargs):
            # run the task inside an app context and log start and finish
            with app.app_context():
                fargs = ' '.join([str(arg) for arg in args])
                fkwargs = ' '.join([f'{k}={v}' for (k, v) in kwargs.items()])

                app.logger.info("STARTING %s %s %s", f.__name__, fargs, fkwargs)

                try:
                    f(*args, **kwargs)
                    app.logger.info("FINISHED %s %s %s", f.__name__, fargs, fkwargs)
                except Exception:
                    app.logger.exception("ERRORED %s %s %s", f.__name__, fargs, fkwargs)

        return decorator

    def composed_decorator(f):
        return huey_decorator(with_context(f))

    return composed_decorator


@feed_cli.command('sync')
@huey_task(crontab(minute=app.config['SYNC_FEEDS_CRON_MINUTES']))
def sync_all_feeds():
    feeds = db.session.execute(db.select(models.Feed.id, models.Feed.name)).all()

    tasks = []
    for feed in feeds:
        tasks.append((feed.name, sync_feed(feed.id, feed.name)))

    # wait for concurrent tasks to finish before returning
    for name, task in tasks:
        try:
            task.get()
        except Exception:
            app.logger.exception("failure during async task %s", name)
            continue


@huey_task()
def sync_feed(feed_id, _feed_name, force=False):
    db_feed = db.session.get(models.Feed, feed_id)
    db_feed.sync_with_remote(force=force)
    db.session.commit()


@feed_cli.command('prefetch')
@huey_task(crontab(minute=app.config['CONTENT_PREFETCH_MINUTES']))
def content_prefetch():
    # fetching and cleaning up the article html is too expensive to do on all articles,
    # but we can prefetch the first few pages of the homepage to improve the experience
    for user_id in db.session.scalars(db.select(models.User.id)):
        start_at = datetime.datetime.utcnow()
        query = models.Entry.sorted_by(
            user_id, models.Entry.ORDER_FREQUENCY, start_at, hide_seen=True) \
            .filter(models.Entry.content_full.is_(None), models.Entry.content_url.isnot(None))\
            .limit(15)

        for entry in db.session.scalars(query):
            app.logger.debug('Prefetching %s', entry.content_url)
            entry.fetch_content()
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
        .filter(models.Entry.sort_date < older_than_date,
                models.Entry.favorited.is_(None),
                models.Entry.backlogged.is_(None),
                models.Entry.pinned.is_(None)
                )\
        .group_by(models.Feed.id)\
        .having(sa.func.count(models.Feed.entries) > 0)

    for (feed_id, feed_name) in db.session.execute(feeds_q).all():
        # of the ones that have old entries, get the date of the nth entry (overall, not just within the old ones)
        min_sort_date = db.session.scalar(
            db.select(models.Entry.sort_date)
            .filter_by(feed_id=feed_id)
            .order_by(models.Entry.sort_date.desc())
            .limit(1)
            .offset(minimum - 1))

        if not min_sort_date:
            continue

        # delete all entries from that feed that are older than RSS_SKIP_OLDER_THAN_DAYS
        # AND ALSO older than the nth entry, so we guarantee to always keep at least the minimum
        q = db.delete(models.Entry)\
              .where(
                  models.Entry.favorited.is_(None),
                  models.Entry.backlogged.is_(None),
                  models.Entry.pinned.is_(None),
                  models.Entry.feed_id == feed_id,
                  models.Entry.sort_date < min_sort_date,
                  models.Entry.sort_date < older_than_date)

        res = db.session.execute(q)
        db.session.commit()
        if res.rowcount:
            app.logger.info("Deleted %s old entries from %s/%s", res.rowcount, feed_id, feed_name)

    # Delete old standalone entries (without associated feed)
    q = db.delete(models.Entry)\
        .where(
        models.Entry.feed_id.is_(None),
        models.Entry.favorited.is_(None),
        models.Entry.pinned.is_(None),
        models.Entry.sort_date < older_than_date)

    res = db.session.execute(q)
    db.session.commit()
    if res.rowcount:
        app.logger.info("Deleted %s old standalone entries from", res.rowcount)


@feed_cli.command('backlog')
@huey_task(crontab(minute='0', hour='0'))
def pop_backlog():
    "Periodically pop an entry from the backlog into the home feed."
    # TODO make this configurable
    week_ago = datetime.datetime.utcnow() - datetime.timedelta(days=7)
    backlogged_date = sa.func.min(models.Entry.backlogged).label('backlogged_date')
    query = db.select(models.Entry)\
        .group_by(models.Entry.user_id)\
        .having(backlogged_date < week_ago)

    for entry in db.session.scalars(query):
        entry.unbacklog()
        app.logger.info("Popped from user %s backlog: %s ", entry.user_id, entry.target_url)

    db.session.commit()


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


@feed_cli.command('dump')
@click.argument("file")
@click.argument('user', required=False, callback=load_user_arg)
def csv_dump(file, user):
    "Dump feeds to a local csv file."

    with open(file, 'w') as csv_file:
        feed_writer = csv.writer(csv_file)
        for feed in db.session.execute(db.select(models.Feed)
                                       .filter_by(user_id=user.id, is_mastodon=False)).scalars():
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
            if feed.folder in folder_outlines:
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
    query = db.select(db.exists(models.Feed)
                      .where(models.Feed.name == feed.name, models.Feed.user_id == feed.user_id))
    if db.session.execute(query).scalar():
        app.logger.info('skipping already existent %s', feed.name)
        return

    db.session.add(feed)
    db.session.commit()

    feed.load_icon()
    db.session.commit()
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
