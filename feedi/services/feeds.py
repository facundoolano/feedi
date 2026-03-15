import csv
import datetime
import json
import logging

import opml
import sqlalchemy.dialects.sqlite as sqlite
from flask import current_app as app

import feedi.models as models
import feedi.parsers.custom as custom_parser
import feedi.parsers.rss as rss_parser
from feedi.models import db
from feedi.parsers.scraping import get_favicon

logger = logging.getLogger(__name__)


def add(user_id, values):
    """Create and persist a new feed, loading its icon. Returns the new feed."""
    feed_cls = models.Feed.resolve(values["type"])
    feed = feed_cls(**values)
    feed.user_id = user_id
    db.session.add(feed)
    db.session.flush()
    load_icon(feed)
    db.session.commit()
    return feed


def delete(feed):
    """Remove a feed, preserving any pinned or favorited entries."""
    # preserve pinned and favorited by moving them out of the feed before deleting it.
    update = (
        db.update(models.Entry)
        .where(
            (models.Entry.feed_id == feed.id) & (models.Entry.favorited.isnot(None) | models.Entry.pinned.isnot(None))
        )
        .values(feed_id=None)
    )
    db.session.execute(update)

    # running from db.session ensures cascading effects
    db.session.delete(feed)
    db.session.commit()


def discover(url):
    """Try to discover an RSS/Atom feed at the given URL."""
    return rss_parser.discover_feed(url)


def sync(feed, force=False):
    """
    Fetch this feed's entries from its remote source, saving them to the database
    and updating feed metadata.
    If `force` is True, syncing will be attempted even if it was already done recently.
    """
    utcnow = datetime.datetime.utcnow()

    cooldown_minutes = datetime.timedelta(minutes=app.config["SKIP_RECENTLY_UPDATED_MINUTES"])
    if not force and feed.last_fetch and (utcnow - feed.last_fetch < cooldown_minutes):
        app.logger.info("skipping recently synced feed %s", feed.name)
        return

    entries = fetch_entry_data(feed, force)
    feed.last_fetch = utcnow

    for values in entries:
        # upsert to handle already seen entries.
        # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
        values["updated"] = utcnow
        values["feed_id"] = feed.id
        values["user_id"] = feed.user_id

        update_values = dict(**values)
        update_values.pop("sort_date", None)
        db.session.execute(
            sqlite.insert(models.Entry)
            .values(**values)
            .on_conflict_do_update(("feed_id", "remote_id"), set_=update_values)
        )

    # Calculate and store bucket after entries are inserted
    feed.bucket = feed.calculate_bucket()
    db.session.commit()


def fetch_entry_data(feed, force=False):
    """Fetch entries from the remote source, dispatching by feed type."""
    if isinstance(feed, models.RssFeed):
        skip_older_than = datetime.datetime.utcnow() - datetime.timedelta(days=app.config["RSS_SKIP_OLDER_THAN_DAYS"])

        feed_data, entries, etag, modified = rss_parser.fetch(
            feed.name,
            feed.url,
            skip_older_than,
            app.config["RSS_MINIMUM_ENTRY_AMOUNT"],
            None if force else feed.last_fetch,
            None if force else feed.etag,
            None if force else feed.modified_header,
            feed.filters,
        )

        feed.etag = etag
        feed.modified_header = modified
        if feed_data:
            feed.raw_data = json.dumps(feed_data)
        return entries

    elif isinstance(feed, models.CustomFeed):
        return custom_parser.fetch(feed.name, feed.url)

    raise ValueError(f"unknown feed type: {feed.type}")


def import_csv(user, file):
    """Load feeds from a CSV file path."""
    with open(file) as csv_file:
        for values in csv.reader(csv_file):
            cls = models.Feed.resolve(values[0])
            feed = cls.from_valuelist(*values)
            feed.user_id = user.id
            _add_if_not_exists(feed)


def export_csv(user, file):
    """Dump feeds to a CSV file path."""
    with open(file, "w") as csv_file:
        feed_writer = csv.writer(csv_file)
        for feed in db.session.execute(db.select(models.Feed).filter_by(user_id=user.id)).scalars():
            feed_writer.writerow(feed.to_valuelist())
            app.logger.info("written %s", feed)


def import_opml(user, file):
    """Load feeds from an OPML file path."""
    document = opml.OpmlDocument.load(file)

    for outline in document.outlines:
        if outline.outlines:
            # it's a folder
            folder = outline.text
            for feed in outline.outlines:
                _add_if_not_exists(
                    models.RssFeed(name=feed.title or feed.text, user_id=user.id, url=feed.xml_url, folder=folder)
                )
        else:
            # it's a top-level feed
            _add_if_not_exists(models.RssFeed(name=outline.title or outline.text, user_id=user.id, url=outline.xml_url))


def export_opml(user, file):
    """Dump RSS feeds to an OPML file path."""
    document = opml.OpmlDocument()
    folder_outlines = {}
    for feed in db.session.execute(db.select(models.RssFeed).filter_by(user_id=user.id)).scalars():
        if feed.folder:
            # to represent folder structure we put the feed in nested outlines
            if feed.folder not in folder_outlines:
                folder_outlines[feed.folder] = document.add_outline(feed.folder)
            target = folder_outlines[feed.folder]
        else:
            # if feed doesn't have a folder, put it in the top level doc
            target = document

        target.add_rss(
            feed.name,
            feed.url,
            title=feed.name,
            categories=[feed.folder] if feed.folder else [],
            created=datetime.datetime.now(),
        )

    document.dump(file)


def recalculate_buckets():
    """Recalculate frequency buckets for all feeds."""
    all_feeds = db.session.query(models.Feed).all()

    for feed in all_feeds:
        old_bucket = feed.bucket
        feed.bucket = feed.calculate_bucket()
        app.logger.info("Feed %s/%s: bucket %s -> %s", feed.id, feed.name, old_bucket, feed.bucket)

    db.session.commit()
    app.logger.info("Recalculated buckets for %s feeds", len(all_feeds))


def _add_if_not_exists(feed):
    query = db.select(db.exists(models.Feed).where(models.Feed.name == feed.name, models.Feed.user_id == feed.user_id))
    if db.session.execute(query).scalar():
        app.logger.info("skipping already existent %s", feed.name)
        return

    db.session.add(feed)
    db.session.commit()

    load_icon(feed)
    db.session.commit()
    app.logger.info("added %s", feed)


def load_icon(feed):
    """Load and store the icon URL for the given feed."""
    if isinstance(feed, models.RssFeed):
        feed.icon_url = rss_parser.fetch_icon(feed.url)
    else:
        feed.icon_url = get_favicon(feed.url)
