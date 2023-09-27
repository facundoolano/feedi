# coding: utf-8

import datetime
import json

import sqlalchemy as sa
import sqlalchemy.dialects.sqlite as sqlite
from flask_sqlalchemy import SQLAlchemy

import feedi.parsers as parsers

# TODO consider adding explicit support for url columns

db = SQLAlchemy()


def init_db(app):
    db.init_app(app)

    @sa.event.listens_for(db.engine, 'connect')
    def on_connect(dbapi_connection, _connection_record):
        # if there's a lock for concurrent access, don't fail forever
        # (TODO figure out how to prevent the lock in the first place?)
        dbapi_connection.execute('pragma busy_timeout=5000')

        # experiment to try holding most of the db in memory
        # this should be ~200mb
        dbapi_connection.execute('pragma cache_size = -195313')

    db.create_all()


class Feed(db.Model):
    """
    Represents an external source of items, e.g. an RSS feed or social app account.
    """
    __tablename__ = 'feeds'

    TYPE_RSS = 'rss'
    TYPE_MASTODON_ACCOUNT = 'mastodon'
    TYPE_MASTODON_NOTIFICATIONS = 'mastodon_notifications'
    TYPE_CUSTOM = 'custom'

    url = sa.Column(sa.String, nullable=False)
    id = sa.Column(sa.Integer, primary_key=True)
    type = sa.Column(sa.String, nullable=False)

    name = sa.Column(sa.String, unique=True, index=True)
    icon_url = sa.Column(sa.String)

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False,
                        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    last_fetch = sa.Column(sa.TIMESTAMP)

    entries = sa.orm.relationship("Entry", back_populates="feed",
                                  cascade="all, delete-orphan", lazy='dynamic')
    raw_data = sa.Column(sa.String, doc="The original feed data received from the feed, as JSON")
    folder = sa.Column(sa.String, index=True)
    score = sa.Column(sa.Integer, default=0, nullable=False,
                      doc="counts how many times articles of this feed have been interacted with. ")

    javascript_enabled = sa.Column(sa.Boolean, default=False)

    __mapper_args__ = {'polymorphic_on': type,
                       'polymorphic_identity': 'feed'}

    def __repr__(self):
        return f'<Feed {self.name}>'

    # TODO can this be avoided?
    @classmethod
    def resolve(cls, type):
        subclasses = {
            cls.TYPE_RSS: RssFeed,
            cls.TYPE_MASTODON_ACCOUNT: MastodonAccount,
            cls.TYPE_MASTODON_NOTIFICATIONS: MastodonNotifications,
            cls.TYPE_CUSTOM: CustomFeed
        }

        subcls = subclasses.get(type)
        if not subcls:
            raise ValueError(f'unknown type {type}')
        return subcls

    def sync_with_remote(self):
        "TODO"
        from flask import current_app as app
        utcnow = datetime.datetime.utcnow()

        cooldown_minutes = datetime.timedelta(minutes=app.config['SKIP_RECENTLY_UPDATED_MINUTES'])
        if self.last_fetch and (utcnow - self.last_fetch < cooldown_minutes):
            app.logger.info('skipping recently synced feed %s', self.name)
            return

        entries = self.fetch_entry_data()
        self.last_fetch = utcnow

        # TODO can this commit be removed?
        db.session.commit()
        for values in entries:
            # upsert to handle already seen entries.
            # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
            values['updated'] = utcnow
            values['feed_id'] = self.id
            db.session.execute(
                sqlite.insert(Entry).
                values(**values).
                on_conflict_do_update(("feed_id", "remote_id"), set_=values)
            )

            # TODO can this commit be removed?
            # inline commit to avoid sqlite locking when fetching parallel feeds
            db.session.commit()

    def fetch_entry_data(self):
        "TODO"
        raise NotImplementedError

    @classmethod
    def frequency_rank_query(cls):
        """
        Count the daily average amount of entries per feed currently in the db
        and put the result into "buckets". The rationale is to show least frequent first,
        but not long sequences of the same feed if there are several at the frequency ballpark.
        """
        from flask import current_app as app
        retention_days = app.config['DELETE_AFTER_DAYS']
        retention_date = datetime.datetime.utcnow() - datetime.timedelta(days=retention_days)
        days_since_creation = 1 + sa.func.min(retention_days, sa.func.round(
            sa.func.julianday('now') - sa.func.julianday(cls.created)))

        # this expression ranks feeds (puts them in "buckets") according to how much daily entries they have on average
        # NOTE: some of this categories are impossible with a low retention period
        # (e.g. we can't distinguish between weekly and monthly if we only keep 5 days or records)
        rank_func = sa.case(
            (sa.func.count(cls.id) / days_since_creation < 1 / 30, 0),  # once a month or less
            (sa.func.count(cls.id) / days_since_creation < 1 / 7, 1),  # once week or less
            (sa.func.count(cls.id) / days_since_creation < 1, 2),  # once a day or less
            (sa.func.count(cls.id) / days_since_creation < 5, 3),  # 5 times a day or less
            else_=4  # more
        )

        return db.select(cls.id, rank_func.label('rank'))\
            .join(Entry)\
            .filter(Entry.remote_updated >= retention_date)\
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
    etag = sa.Column(
        sa.String, doc="Etag received on last parsed rss, to prevent re-fetching if it hasn't changed.")
    modified_header = sa.Column(
        sa.String, doc="Last-modified received on last parsed rss, to prevent re-fetching if it hasn't changed.")

    filters = sa.Column(
        sa.String, doc="a comma separated list of conditions that feed source entries need to meet to be included in the feed.")

    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_RSS}

    def fetch_entry_data(self):
        from flask import current_app as app
        skip_older_than = datetime.datetime.utcnow() - \
            datetime.timedelta(days=app.config['RSS_SKIP_OLDER_THAN_DAYS'])

        feed_data, entries, etag, modified = parsers.rss.fetch(
            self.name, self.url,
            skip_older_than,
            app.config['RSS_MINIMUM_ENTRY_AMOUNT'],
            self.last_fetch,
            self.etag,
            self.modified_header,
            self.filters)

        self.etag = etag
        self.modified_header = modified
        if feed_data:
            self.raw_data = json.dumps(feed_data)
        return entries


class MastodonAccount(Feed):
    access_token = sa.Column(sa.String)

    def _api_args(self):
        from flask import current_app as app
        latest_entry = self.entries.order_by(Entry.remote_updated.desc()).first()
        args = dict(server_url=self.url, access_token=self.access_token)
        if latest_entry:
            # there's some entry on db, this is not the first time we're syncing
            # get all toots since the last seen one
            args['newer_than'] = latest_entry.remote_id
        else:
            # if there isn't any entry yet, get the "first page" of toots from the timeline
            args['limit'] = app.config['MASTODON_FETCH_LIMIT']
        return args

    def fetch_entry_data(self):
        return parsers.mastodon.fetch_toots(**self._api_args())

    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_MASTODON_ACCOUNT}


class MastodonNotifications(MastodonAccount):

    def fetch_entry_data(self):
        return parsers.mastodon.fetch_notifications(**self._api_args())

    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_MASTODON_NOTIFICATIONS}


class CustomFeed(Feed):
    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_CUSTOM}

    def fetch_entry_data(self):
        return parsers.custom.fetch(self.name, self.url)


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

    avatar_url = sa.Column(
        sa.String, doc="The url of the avatar image to be displayed for the entry.")

    body = sa.Column(
        sa.String, doc="The content to be displayed in the feed preview. HTML is supported. For article entries, it would be an excerpt of the full article content.")
    entry_url = sa.Column(
        sa.String, doc="The URL of this entry in the source. For link aggregators this would be the comments page.")
    content_url = sa.Column(
        sa.String, doc="The URL where the full content can be fetched or read. For link aggregators this would be the article redirect url. An empty content URL implies that the entry can't be read locally.")
    media_url = sa.Column(sa.String, doc="URL of a media attachement or preview.")

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False,
                        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    remote_created = sa.Column(sa.TIMESTAMP, nullable=False)
    remote_updated = sa.Column(sa.TIMESTAMP, nullable=False)

    viewed = sa.Column(sa.TIMESTAMP, index=True)
    favorited = sa.Column(sa.TIMESTAMP, index=True)
    pinned = sa.Column(sa.TIMESTAMP, index=True)

    raw_data = sa.Column(sa.String, doc="The original entry data received from the feed, as JSON")

    header = sa.Column(
        sa.String, doc="an html line to put above the title, such as 'user reblogged'.")

    __table_args__ = (sa.UniqueConstraint("feed_id", "remote_id"),
                      sa.Index("entry_updated_ts", remote_updated.desc()))

    def __repr__(self):
        return f'<Entry {self.feed_id}/{self.remote_id}>'

    @classmethod
    def _filtered_query(cls, hide_seen=False, favorited=None,
                        feed_name=None, username=None, folder=None,
                        older_than=None, text=None):
        """
        Return a base Entry query applying any combination of filters.
        """

        query = db.select(cls)

        if older_than:
            query = query.filter(cls.created < older_than)

            if hide_seen:
                # We use older_than so we don't exclude viewed entries from the current pagination "session"
                # (those previous entries need to be included for a correct calculation of the limit/offset
                # next time a page is fetch).
                query = query.filter(cls.viewed.is_(None) |
                                     (cls.viewed.isnot(None) & (cls.viewed > older_than)))

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
