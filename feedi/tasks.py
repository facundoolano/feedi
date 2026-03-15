"""
Background tasks scheduled by Huey.
"""

import datetime
from functools import wraps

from huey import crontab
from huey.contrib.mini import MiniHuey

import sqlalchemy as sa

import feedi.models as models
from feedi.app import create_huey_app
from feedi.models import db
from feedi.services import entries, feeds

app = create_huey_app()
huey = MiniHuey(pool_size=app.config["HUEY_POOL_SIZE"])


def huey_task(*huey_args):
    "Wraps a function to make a it a MiniHuey task that is run inside a flask app context."

    huey_decorator = huey.task(*huey_args)

    def with_context(f):
        @wraps(f)
        def decorator(*args, **kwargs):
            # run the task inside an app context and log start and finish
            with app.app_context():
                fargs = " ".join([str(arg) for arg in args])
                fkwargs = " ".join([f"{k}={v}" for (k, v) in kwargs.items()])

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


@huey_task(crontab(minute=app.config["SYNC_FEEDS_CRON_MINUTES"]))
def sync_all_feeds():
    all_feeds = db.session.execute(db.select(models.Feed.id, models.Feed.name)).all()

    subtasks = []
    for feed in all_feeds:
        subtasks.append((feed.name, sync_feed(feed.id, feed.name)))

    # wait for concurrent tasks to finish before returning
    for name, task in subtasks:
        try:
            task.get()
        except Exception:
            app.logger.exception("failure during async task %s", name)
            continue


@huey_task()
def sync_feed(feed_id, _feed_name, force=False):
    feed = db.session.get(models.Feed, feed_id)
    feeds.sync(feed, force=force)
    db.session.commit()


@huey_task(crontab(minute=app.config["CONTENT_PREFETCH_MINUTES"]))
def content_prefetch():
    for user_id in db.session.scalars(db.select(models.User.id)):
        start_at = datetime.datetime.utcnow()
        query = (
            models.Entry.filter_by(user_id, start_at, hide_seen=True)
            .filter(models.Entry.content_full.is_(None), models.Entry.content_url.isnot(None))
            .limit(15)
        )

        for entry in db.session.scalars(query):
            app.logger.debug("Prefetching %s", entry.content_url)
            entries.fetch_content(entry)
            db.session.commit()


@huey_task(crontab(minute="0", hour=app.config["DELETE_OLD_CRON_HOURS"]))
def delete_old_entries():
    """
    Delete entries that are older than DELETE_AFTER_DAYS but
    making sure we always keep RSS_MINIMUM_ENTRY_AMOUNT for each feed.
    Favorite and pinned entries aren't deleted.
    """
    older_than_date = datetime.datetime.utcnow() - datetime.timedelta(days=app.config["DELETE_AFTER_DAYS"])
    minimum = app.config["RSS_MINIMUM_ENTRY_AMOUNT"]
    # there must be more clever sql ways to do this, but it doesn't have to be efficient

    # filter feeds that have old entries
    feeds_q = (
        db.select(models.Feed.id, models.Feed.name)
        .join(models.Feed.entries)
        .filter(
            models.Entry.sort_date < older_than_date,
            models.Entry.favorited.is_(None),
            models.Entry.sent_to_kindle.is_(None),
            models.Entry.pinned.is_(None),
        )
        .group_by(models.Feed.id)
        .having(sa.func.count(models.Feed.entries) > 0)
    )

    for feed_id, feed_name in db.session.execute(feeds_q).all():
        # of the ones that have old entries, get the date of the nth entry (overall, not just within the old ones)
        min_sort_date = db.session.scalar(
            db.select(models.Entry.sort_date)
            .filter_by(feed_id=feed_id)
            .order_by(models.Entry.sort_date.desc())
            .limit(1)
            .offset(minimum - 1)
        )

        if not min_sort_date:
            continue

        # delete all entries from that feed that are older than RSS_SKIP_OLDER_THAN_DAYS
        # AND ALSO older than the nth entry, so we guarantee to always keep at least the minimum
        q = db.delete(models.Entry).where(
            models.Entry.favorited.is_(None),
            models.Entry.sent_to_kindle.is_(None),
            models.Entry.pinned.is_(None),
            models.Entry.feed_id == feed_id,
            models.Entry.sort_date < min_sort_date,
            models.Entry.sort_date < older_than_date,
        )

        res = db.session.execute(q)
        db.session.commit()
        if res.rowcount:
            app.logger.info("Deleted %s old entries from %s/%s", res.rowcount, feed_id, feed_name)

    # Delete old standalone entries (without associated feed)
    q = db.delete(models.Entry).where(
        models.Entry.feed_id.is_(None),
        models.Entry.sent_to_kindle.is_(None),
        models.Entry.favorited.is_(None),
        models.Entry.pinned.is_(None),
        models.Entry.sort_date < older_than_date,
    )

    res = db.session.execute(q)
    db.session.commit()
    if res.rowcount:
        app.logger.info("Deleted %s old standalone entries", res.rowcount)
