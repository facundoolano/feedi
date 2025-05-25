import datetime
import json
import logging
import urllib

import sqlalchemy as sa
import sqlalchemy.dialects.sqlite as sqlite
import werkzeug.security as security
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy

import feedi.parsers as parsers
from feedi import scraping

# TODO consider adding explicit support for url columns

db = SQLAlchemy()


logger = logging.getLogger(__name__)


def init_db(app):
    db.init_app(app)

    @sa.event.listens_for(db.engine, "connect")
    def on_connect(dbapi_connection, _connection_record):
        # use WAL mode to prevent locks on concurrent writes
        dbapi_connection.execute("pragma journal_mode=WAL")

        # experiment to try holding most of the db in memory
        # this should be ~200mb
        dbapi_connection.execute("pragma cache_size = -195313")

        app.logger.debug("Created DB connection")

    @sa.event.listens_for(User.__table__, "after_create")
    def after_create(user_table, connection, **kw):
        email = app.config.get("DEFAULT_AUTH_USER")
        if email:
            app.logger.info("Creating default user %s", email)
            stmt = sa.insert(user_table).values(email=email, password=security.generate_password_hash("admin"))
            connection.execute(stmt)

    db.create_all()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = sa.Column(sa.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

    kindle_email = db.Column(db.String(100))

    @staticmethod
    def hash_password(raw_password):
        return security.generate_password_hash(raw_password)

    def set_password(self, raw_password):
        self.password = security.generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return security.check_password_hash(self.password, raw_password)


class Feed(db.Model):
    """
    Represents an external source of items, e.g. an RSS feed or social app account.
    """

    __tablename__ = "feeds"

    TYPE_RSS = "rss"
    TYPE_CUSTOM = "custom"

    id = sa.Column(sa.Integer, primary_key=True)
    user_id = sa.orm.mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)

    url = sa.Column(sa.String)
    type = sa.Column(sa.String, nullable=False)

    name = sa.Column(sa.String)
    icon_url = sa.Column(sa.String)

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, index=True)
    updated = sa.Column(
        sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
    last_fetch = sa.Column(sa.TIMESTAMP)

    entries = sa.orm.relationship("Entry", back_populates="feed", cascade="all, delete-orphan", lazy="dynamic")
    raw_data = sa.orm.deferred(sa.Column(sa.String, doc="The original feed data received from the feed, as JSON"))

    folder = sa.Column(sa.String, index=True)

    __mapper_args__ = {"polymorphic_on": type, "polymorphic_identity": "feed"}

    __table_args__ = (sa.UniqueConstraint("user_id", "name"), sa.Index("ix_name_user", "user_id", "name"))

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name}>"

    @classmethod
    def resolve(cls, type):
        "Return the Feed model subclass for the given feed type."
        subclasses = {
            cls.TYPE_RSS: RssFeed,
            cls.TYPE_CUSTOM: CustomFeed,
        }

        subcls = subclasses.get(type)
        if not subcls:
            raise ValueError(f"unknown type {type}")
        return subcls

    @classmethod
    def from_valuelist(cls, type, name, url, folder):
        return cls(**dict(type=type, name=name, url=url, folder=folder))

    def to_valuelist(self):
        return [self.type, self.name, self.url, self.folder]

    def sync_with_remote(self, force=False):
        """
        Fetch this feed entries from its remote sources, saving them to the database and updating
        the feed metadata. The specific fetching logic is implemented by subclasses through the
        `fetch_entry_data` method.
        If `force` is True, syncing will be attempted even if it was already done recently.
        """
        from flask import current_app as app

        utcnow = datetime.datetime.utcnow()

        cooldown_minutes = datetime.timedelta(minutes=app.config["SKIP_RECENTLY_UPDATED_MINUTES"])
        if not force and self.last_fetch and (utcnow - self.last_fetch < cooldown_minutes):
            app.logger.info("skipping recently synced feed %s", self.name)
            return

        entries = self.fetch_entry_data(force)
        self.last_fetch = utcnow

        for values in entries:
            # upsert to handle already seen entries.
            # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
            values["updated"] = utcnow
            values["feed_id"] = self.id
            values["user_id"] = self.user_id
            db.session.execute(
                sqlite.insert(Entry).values(**values).on_conflict_do_update(("feed_id", "remote_id"), set_=values)
            )

    def fetch_entry_data(self, _force=False):
        """
        To be implemented by subclasses, this should contact the remote feed source, parse any new entries
        and return a list of values for each one.
        """
        raise NotImplementedError

    def load_icon(self):
        ""
        self.icon_url = scraping.get_favicon(self.url)

    @classmethod
    def frequency_rank_query(cls):
        """
        Count the daily average amount of entries per feed currently in the db
        and put the result into "buckets". The rationale is to show least frequent first,
        but not long sequences of the same feed if there are several at the frequency ballpark.
        """
        from flask import current_app as app

        retention_days = app.config["DELETE_AFTER_DAYS"]
        retention_date = datetime.datetime.utcnow() - datetime.timedelta(days=retention_days)
        days_since_creation = 1 + sa.func.min(
            retention_days, sa.func.round(sa.func.julianday("now") - sa.func.julianday(cls.created))
        )

        # this expression ranks feeds (puts them in "buckets") according to how much daily entries they have on average
        # NOTE: some of this categories are impossible with a low retention period
        # (e.g. we can't distinguish between weekly and monthly if we only keep 5 days or records)
        rank_func = sa.case(
            (sa.func.count(cls.id) / days_since_creation < 1 / 30, 0),  # once a month or less
            (sa.func.count(cls.id) / days_since_creation < 1 / 7, 1),  # once week or less
            (sa.func.count(cls.id) / days_since_creation < 1, 2),  # once a day or less
            (sa.func.count(cls.id) / days_since_creation < 5, 3),  # 5 times a day or less
            (sa.func.count(cls.id) / days_since_creation < 20, 4),  # 20 times a day or less
            else_=5,  # more
        )

        return (
            db.select(cls.id, rank_func.label("rank"))
            .join(Entry)
            .filter(Entry.sort_date >= retention_date)
            .group_by(cls)
            .subquery()
        )

    def frequency_rank(self):
        """
        Return the frequency rank of this feed.
        """
        subquery = self.frequency_rank_query()
        query = db.select(subquery.c.rank).select_from(Feed).join(subquery, subquery.c.id == self.id)
        return db.session.scalar(query)


class RssFeed(Feed):
    etag = sa.Column(sa.String, doc="Etag received on last parsed rss, to prevent re-fetching if it hasn't changed.")
    modified_header = sa.Column(
        sa.String, doc="Last-modified received on last parsed rss, to prevent re-fetching if it hasn't changed."
    )

    filters = sa.Column(
        sa.String,
        doc="a comma separated list of conditions that feed source entries need to meet \
                        to be included in the feed.",
    )

    __mapper_args__ = {"polymorphic_identity": Feed.TYPE_RSS}

    @classmethod
    def from_valuelist(cls, _type, name, url, folder, filters):
        return cls(**dict(name=name, url=url, folder=folder, filters=filters))

    def to_valuelist(self):
        return [self.type, self.name, self.url, self.folder, self.filters]

    def fetch_entry_data(self, force=False):
        from flask import current_app as app

        skip_older_than = datetime.datetime.utcnow() - datetime.timedelta(days=app.config["RSS_SKIP_OLDER_THAN_DAYS"])

        feed_data, entries, etag, modified = parsers.rss.fetch(
            self.name,
            self.url,
            skip_older_than,
            app.config["RSS_MINIMUM_ENTRY_AMOUNT"],
            None if force else self.last_fetch,
            None if force else self.etag,
            None if force else self.modified_header,
            self.filters,
        )

        self.etag = etag
        self.modified_header = modified
        if feed_data:
            self.raw_data = json.dumps(feed_data)
        return entries

    def load_icon(self):
        self.icon_url = parsers.rss.fetch_icon(self.url)


class CustomFeed(Feed):
    __mapper_args__ = {"polymorphic_identity": Feed.TYPE_CUSTOM}

    def fetch_entry_data(self, _force=False):
        return parsers.custom.fetch(self.name, self.url)


class Entry(db.Model):
    """
    Represents an item within a Feed.
    """

    __tablename__ = "entries"

    id = sa.Column(sa.Integer, primary_key=True)

    feed_id = sa.orm.mapped_column(sa.ForeignKey("feeds.id"))
    user_id = sa.orm.mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)
    feed = sa.orm.relationship("Feed", back_populates="entries")
    remote_id = sa.Column(sa.String, nullable=False, doc="The identifier of this entry in its source feed.")

    title = sa.Column(sa.String)
    username = sa.Column(sa.String, index=True)
    user_url = sa.Column(sa.String)
    display_name = sa.Column(sa.String, doc="For cases where there's a full display name in addition to username.")

    avatar_url = sa.Column(sa.String, doc="The url of the avatar image to be displayed for the entry.")

    content_short = sa.Column(
        sa.String,
        doc="The content to be displayed in the feed preview. HTML is supported. \
    For article entries, it would be an excerpt of the full article content.",
    )

    content_full = sa.orm.deferred(
        sa.Column(sa.String, doc="The content to be displayed in the reader, e.g. the cleaned full article HTML.")
    )

    target_url = sa.Column(
        sa.String,
        doc="The URL to open when accessing the entry at its source. \
        NULL is interpreted as the entry cannot be open at the source.",
    )

    content_url = sa.Column(
        sa.String,
        doc="The URL to fetch the full entry content from, for reading locally. \
        NULL is interpreted as the entry cannot be read locally.",
    )

    comments_url = sa.Column(
        sa.String,
        doc="The URL to fetch the full entry content from, for reading locally. \
        NULL is interpreted as the entry cannot be read locally.",
    )

    media_url = sa.Column(sa.String, doc="URL of a media attachement or preview.")

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, index=True)
    updated = sa.Column(
        sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow
    )
    display_date = sa.Column(
        sa.TIMESTAMP,
        nullable=False,
        doc="The date that will displayed as the publication date of the entry. \
                             Typically the publication or creation date informed at the source.",
    )

    sort_date = sa.Column(
        sa.TIMESTAMP,
        nullable=False,
        index=True,
        doc="The date that determines an entry's chronological order. \
                          Typically the updated date informed at the source.",
    )

    viewed = sa.Column(sa.TIMESTAMP, index=True)
    favorited = sa.Column(sa.TIMESTAMP, index=True)
    pinned = sa.Column(sa.TIMESTAMP, index=True)

    sent_to_kindle = sa.Column(sa.TIMESTAMP, index=True)

    raw_data = sa.orm.deferred(sa.Column(sa.String, doc="The original entry data received from the feed, as JSON"))

    header = sa.Column(sa.String, doc="an html line to put above the title, such as 'user reblogged'.")

    icon_url = sa.Column(
        sa.String, doc="To be used for standalone entry avatars or as a fallback when the feed has no icon."
    )

    __table_args__ = (sa.UniqueConstraint("feed_id", "remote_id"), sa.Index("entry_sort_ts", sort_date.desc()))

    @classmethod
    def from_url(cls, user_id, url):
        "Load an entry for the given article url if it exists, otherwise create a new one."
        entry = db.session.scalar(db.select(cls).filter_by(content_url=url, user_id=user_id))

        if not entry:
            values = parsers.html.fetch(url)
            entry = cls(user_id=user_id, **values)
        return entry

    def __repr__(self):
        return f"<Entry {self.feed_id}/{self.remote_id}>"

    @property
    def is_external_link(self):
        """
        Return True if the target url seems to be external to the source, e.g. a link submitted to a link aggregator,
        or a preview url. This is handy to decide whether a new RSS feed may be discoverable from an entry. This will
        incorrectly return True if the rss feed is hosted at a different domain than the actual source site it exposes.
        """
        if not self.target_url:
            return False

        if not self.feed:
            return True

        if not self.feed.url:
            return False

        return urllib.parse.urlparse(self.target_url).netloc != urllib.parse.urlparse(self.feed.url)

    @property
    def has_distinct_user(self):
        """
        Returns True if this entry has a recognizable author, particularly that
        it has an avatar and a name that can be displayed instead of a generic feed icon.
        """
        return self.avatar_url and (self.display_name or self.username)

    def fetch_content(self):
        if self.content_url and not self.content_full:
            try:
                self.content_full = scraping.extract(self.content_url)["content"]
            except Exception as e:
                logger.debug("failed to fetch content %s", e)

    @classmethod
    def _filtered_query(
        cls,
        user_id,
        hide_seen=False,
        favorited=None,
        sent_to_kindle=None,
        feed_name=None,
        username=None,
        folder=None,
        older_than=None,
        newer_than=None,
        text=None,
    ):
        """
        Return a base Entry query applying any combination of filters.
        """

        query = db.select(cls).filter_by(user_id=user_id)

        if older_than:
            query = query.filter(cls.created < older_than)

            if hide_seen:
                # We use older_than so we don't exclude viewed entries from the current pagination "session"
                # (those previous entries need to be included for a correct calculation of the limit/offset
                # next time a page is fetch).
                query = query.filter(cls.viewed.is_(None) | (cls.viewed.isnot(None) & (cls.viewed > older_than)))

        if newer_than:
            query = query.filter(cls.created > newer_than)

        if favorited:
            query = query.filter(cls.favorited.is_not(None))

        if sent_to_kindle:
            query = query.filter(cls.sent_to_kindle.is_not(None))

        if feed_name:
            query = query.filter(cls.feed.has(name=feed_name))

        if folder:
            query = query.filter(cls.feed.has(folder=folder))

        if username:
            query = query.filter(cls.username == username)

        if text:
            # Poor Text Search™
            query = query.filter(
                cls.title.contains(text)
                | cls.username.contains(text)
                | cls.content_short.contains(text)
                | cls.content_full.contains(text)
            )

        return query

    @classmethod
    def select_pinned(cls, user_id, **kwargs):
        "Return the full list of pinned entries considering the optional filters."
        query = cls._filtered_query(user_id, **kwargs).filter(cls.pinned.is_not(None)).order_by(cls.pinned.desc())

        return db.session.scalars(query).all()

    @classmethod
    def filter_by(cls, user_id, start_at, **filters):
        """
        Return a query to filter entries added after the `start_at` datetime,
        sorted according to the specified `ordering` criteria and with optional filters.
        """
        query = cls._filtered_query(user_id, older_than=start_at, **filters)

        if filters.get("favorited"):
            return query.order_by(cls.favorited.desc())

        elif filters.get("sent_to_kindle"):
            return query.order_by(cls.sent_to_kindle.desc())

        # Order entries by least frequent feeds first then reverse-chronologically for entries in the same
        # frequency rank.
        subquery = Feed.frequency_rank_query()

        # exhaust last n hours of all ranks before moving to older stuff
        # if smaller delta, more chances to bury infrequent posts
        # if bigger, more chances to bury recent stuff under old unseen infrequent posts
        recency_bucket_date = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

        # isouter = true so that if a feed with only old stuff is added, entries still show up
        # even without having a freq rank
        return (
            query.join(Feed, isouter=True)
            .join(subquery, Feed.id == subquery.c.id, isouter=True)
            .order_by((cls.sort_date >= recency_bucket_date).desc(), subquery.c.rank, cls.sort_date.desc())
        )
