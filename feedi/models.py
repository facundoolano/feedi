# coding: utf-8

import datetime
import math

import sqlalchemy as sa
from flask_sqlalchemy import SQLAlchemy

# TODO consider adding explicit support for url columns

db = SQLAlchemy()


def init_db(app):
    db.init_app(app)

    @sa.event.listens_for(db.engine, 'connect')
    def on_connect(dbapi_connection, _connection_record):
        # math functions are not available by default in sqlite
        # so registering log as a custom function on connection
        dbapi_connection.create_function('log', 1, math.log)

    db.create_all()


class Feed(db.Model):
    """
    Represents an external source of items, e.g. an RSS feed or social app account.
    """
    __tablename__ = 'feeds'

    TYPE_RSS = 'rss'
    TYPE_MASTODON_ACCOUNT = 'mastodon'

    id = sa.Column(sa.Integer, primary_key=True)
    type = sa.Column(sa.String, nullable=False)

    name = sa.Column(sa.String, unique=True, index=True)
    icon_url = sa.Column(sa.String)

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False,
                        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    entries = sa.orm.relationship("Entry", back_populates="feed",
                                  cascade="all, delete-orphan", lazy='dynamic')
    raw_data = sa.Column(sa.String, doc="The original feed data received from the feed, as JSON")
    folder = sa.Column(sa.String, index=True)
    score = sa.Column(sa.Integer, default=0, nullable=False,
                      doc="counts how many times articles of this feed have been interacted with. ")

    __mapper_args__ = {'polymorphic_on': type,
                       'polymorphic_identity': 'feed'}

    def __repr__(self):
        return f'<Feed {self.name}>'

    @classmethod
    def frequency_rank_query(cls):
        """
        Count the daily average amount of entries per feed seen in the last two weeks
        and put the result into "buckets". The rationale is to show least frequent first,
        but not long sequences of the same feed if there are several at the "same order" of frequency.
        """
        two_weeks_ago = datetime.datetime.utcnow() - datetime.timedelta(days=14)
        days_since_creation = 1 + sa.func.min(14, sa.func.round(
            sa.func.julianday('now') - sa.func.julianday(cls.created)))
        rank_func = sa.func.round(sa.func.log(sa.func.round(
            (sa.func.count(cls.id) / days_since_creation * 10))))
        return db.select(cls.id, rank_func.label('rank'))\
            .join(Entry)\
            .filter(Entry.remote_updated >= two_weeks_ago)\
            .group_by(cls)\
            .subquery()

    def frequency_rank(self):
        """
        Return the frequency rank of this feed.
        """
        subquery = self.frequency_rank_query()
        query = db.select(subquery.c.rank)\
                  .select_from(Feed)\
                  .join(subquery, subquery.c.id == self.id)
        return db.session.scalar(query)


class RssFeed(Feed):
    url = sa.Column(sa.String)
    last_fetch = sa.Column(sa.TIMESTAMP)
    etag = sa.Column(
        sa.String, doc="Etag received on last parsed rss, to prevent re-fetching if it hasn't changed.")
    modified_header = sa.Column(
        sa.String, doc="Last-modified received on last parsed rss, to prevent re-fetching if it hasn't changed.")

    __mapper_args__ = {'polymorphic_identity': 'rss'}


class MastodonAccount(Feed):
    # TODO this could be a fk to a separate table with client/secret
    # to share the feedi app across accounts of that same server
    server_url = sa.Column(sa.String)
    access_token = sa.Column(sa.String)

    __mapper_args__ = {'polymorphic_identity': 'mastodon'}


class Entry(db.Model):
    """
    Represents an item within a Feed.
    """

    "Sort entries in reverse chronological order."
    ORDER_RECENCY = 'recency'

    "Sort entries based on the parent's Feeds.score value."
    ORDER_SCORE = 'score'

    "Sort entries based on the post frequency of the parent feed."
    ORDER_FREQUENCY = 'frequency'

    __tablename__ = 'entries'

    id = sa.Column(sa.Integer, primary_key=True)

    feed_id = sa.orm.mapped_column(sa.ForeignKey("feeds.id"))
    feed = sa.orm.relationship("Feed", back_populates="entries")
    remote_id = sa.Column(sa.String, nullable=False,
                          doc="The identifier of this entry in its source feed.")

    title = sa.Column(sa.String, nullable=False)
    username = sa.Column(sa.String, index=True)
    user_url = sa.Column(sa.String, doc="The url of the user that authored the entry.")
    avatar_url = sa.Column(
        sa.String, doc="The url of the avatar image to be displayed for the entry.")

    body = sa.Column(
        sa.String, doc="The content to be displayed in the feed preview. HTML is supported. For article entries, it would be an excerpt of the full article content.")
    entry_url = sa.Column(
        sa.String, doc="The URL of this entry in the source. For link aggregators this would be the comments page.")
    content_url = sa.Column(
        sa.String, doc="The URL where the full content can be fetched or read. For link aggregators this would be the article redirect url.")
    media_url = sa.Column(sa.String, doc="URL of a media attachement or preview.")

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False,
                        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    remote_created = sa.Column(sa.TIMESTAMP, nullable=False)
    remote_updated = sa.Column(sa.TIMESTAMP, nullable=False)

    deleted = sa.Column(sa.TIMESTAMP, index=True)
    favorited = sa.Column(sa.TIMESTAMP, index=True)
    pinned = sa.Column(sa.TIMESTAMP, index=True)

    raw_data = sa.Column(sa.String, doc="The original entry data received from the feed, as JSON")

    # mastodon specific
    reblogged_by = sa.Column(sa.String)

    __table_args__ = (sa.UniqueConstraint("feed_id", "remote_id"),
                      sa.Index("entry_updated_ts", remote_updated.desc()))

    def __repr__(self):
        return f'<Entry {self.feed_id}/{self.remote_id}>'

    @classmethod
    def _filtered_query(cls, deleted=None, favorited=None,
                        feed_name=None, username=None, folder=None,
                        older_than=None, text=None):
        """
        Return a base Entry query applying any combination of filters.
        """

        query = db.select(cls)

        if older_than:
            query = query.filter(cls.updated < older_than)

        if deleted:
            query = query.filter(cls.deleted.is_not(None))
        else:
            query = query.filter(cls.deleted.is_(None))

        if favorited:
            query = query.filter(cls.favorited.is_not(None))

        if feed_name:
            query = query.filter(cls.feed.has(name=feed_name))

        if folder:
            query = query.filter(cls.feed.has(folder=folder))

        if username:
            query = query.filter(cls.username == username)

        if text:
            # Poor Text Search™
            query = query.filter(cls.title.contains(text) |
                                 cls.username.contains(text) |
                                 cls.body.contains(text))

        return query

    @classmethod
    def select_pinned(cls, **kwargs):
        "Return the full list of pinned entries considering the optional filters."
        query = cls._filtered_query(**kwargs)\
                   .filter(cls.pinned.is_not(None))\
                   .order_by(cls.pinned.desc())

        return db.session.scalars(query).all()

    @classmethod
    def sorted_by(cls, ordering, start_at, **filters):
        """
        Return a query to filter entries added after the `start_at` datetime,
        sorted according to the specified `ordering` criteria and with optional filters.
        """
        query = cls._filtered_query(older_than=start_at, **filters)

        if ordering == cls.ORDER_RECENCY:
            # reverse chronological order
            return query.order_by(cls.remote_updated.desc())

        elif ordering == cls.ORDER_SCORE:
            # order by score but within 6 hour buckets, so we don't get everything from the top score feed
            # first, then the 2nd, etc
            return query.join(Feed)\
                        .order_by(
                            sa.func.DATE(cls.remote_updated).desc(),
                            sa.func.round(sa.func.extract('hour', cls.remote_updated) / 6).desc(),
                            Feed.score.desc(),
                            cls.remote_updated.desc())

        elif ordering == cls.ORDER_FREQUENCY:
            # Order entries by least frequent feeds first then reverse-chronologically for entries in the same
            # frequency rank.
            subquery = Feed.frequency_rank_query()

            # by ordering with a "is it older than 24hs?" column we effectively get all entries from the last day first,
            # without excluding the rest --i.e. without truncating the feed after today's entries
            last_day = start_at - datetime.timedelta(hours=24)
            return query.join(Feed)\
                        .join(subquery, subquery.c.id == Feed.id)\
                        .order_by(
                            cls.remote_updated < last_day,
                            subquery.c.rank,
                            cls.remote_updated.desc())
        else:
            raise ValueError('unknown ordering %s' % ordering)
