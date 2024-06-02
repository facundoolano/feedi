# coding: utf-8

import datetime
import json
import logging
import urllib

import sqlalchemy as sa
import sqlalchemy.dialects.sqlite as sqlite
import werkzeug.security as security
from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.ext.hybrid import hybrid_property

import feedi.parsers as parsers
from feedi import scraping

# TODO consider adding explicit support for url columns

db = SQLAlchemy()


logger = logging.getLogger(__name__)


def init_db(app):
    db.init_app(app)

    @sa.event.listens_for(db.engine, 'connect')
    def on_connect(dbapi_connection, _connection_record):
        # use WAL mode to prevent locks on concurrent writes
        dbapi_connection.execute('pragma journal_mode=WAL')

        # experiment to try holding most of the db in memory
        # this should be ~200mb
        dbapi_connection.execute('pragma cache_size = -195313')

        app.logger.debug("Created DB connection")

    @sa.event.listens_for(User.__table__, 'after_create')
    def after_create(user_table, connection, **kw):
        email = app.config.get('DEFAULT_AUTH_USER')
        if email:
            app.logger.info("Creating default user %s", email)
            stmt = sa.insert(user_table).values(
                email=email, password=security.generate_password_hash('admin'))
            connection.execute(stmt)

    db.create_all()


class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = sa.Column(sa.Integer, primary_key=True)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False)

    kindle_email = db.Column(db.String(100))
    mastodon_accounts = sa.orm.relationship("MastodonAccount", back_populates='user')

    @staticmethod
    def hash_password(raw_password):
        return security.generate_password_hash(raw_password)

    def set_password(self, raw_password):
        self.password = security.generate_password_hash(raw_password)

    def check_password(self, raw_password):
        return security.check_password_hash(self.password, raw_password)


class MastodonApp(db.Model):
    """
    Represents the feedi app as registered in a particular mastodon instance. This app has associated
    credentials to generate access tokens for all mastodon accounts in that instance.
    Credentials are persisted so the app is still available for subsequent account registrations for a given
    instance.
    """
    __tablename__ = 'mastodon_apps'
    id = sa.Column(sa.Integer, primary_key=True)
    api_base_url = sa.Column(sa.String, nullable=False)
    client_id = sa.Column(sa.String, nullable=False)
    client_secret = sa.Column(sa.String, nullable=False)

    accounts = sa.orm.relationship("MastodonAccount", back_populates="app")

    @classmethod
    def get_or_create(cls, api_base_url):
        """
        If a feedi app was already registered at the given mastodon instance, fetch it from
        db and return it, otherwise register a new one and store it.
        """
        app = db.session.scalar(db.select(MastodonApp).filter_by(api_base_url=api_base_url))
        if not app:
            client_id, client_secret = parsers.mastodon.register_app(
                api_base_url, cls._oauth_callback_url(api_base_url))
            app = cls(api_base_url=api_base_url,
                      client_id=client_id,
                      client_secret=client_secret)
            db.session.add(app)
            db.session.commit()
        return app

    def auth_redirect_url(self):
        """
        Get the url to redirect to to request access to a user account in the instance
        where this app is registered.
        """
        return parsers.mastodon.auth_redirect_url(self.api_base_url,
                                                  self.client_id,
                                                  self.client_secret,
                                                  self._oauth_callback_url(self.api_base_url))

    def create_account(self, user_id, oauth_code):
        "Given an oauth authorization code from this app, create a new mastodon user account."
        access_token = parsers.mastodon.oauth_login(self.api_base_url,
                                                    self.client_id,
                                                    self.client_secret,
                                                    self._oauth_callback_url(self.api_base_url),
                                                    oauth_code)

        account_data = parsers.mastodon.fetch_account_data(self.api_base_url, access_token)
        domain = self.api_base_url.split('//')[-1]
        username = f"{account_data['username']}@{domain}"

        masto_acct = MastodonAccount(app_id=self.id,
                                     user_id=user_id,
                                     username=username,
                                     access_token=access_token)
        db.session.add(masto_acct)
        db.session.commit()
        return masto_acct

    @staticmethod
    def _oauth_callback_url(api_base_url):
        # the callback url contains the api_base_url because we need to know which app a callback belongs to
        # and we can't use eg. the app id because it needs to be known before creating it, since it's passed
        # to the registration api call
        import flask
        return flask.url_for('mastodon_oauth_callback',
                             server=api_base_url,
                             _external=True)


class MastodonAccount(db.Model):
    """
    Contains an access token for a user account on a specific mastodon instance.
    The same access token can be used for more than one feed (e.g. home and notifications).
    """
    __tablename__ = 'mastodon_accounts'
    id = sa.Column(sa.Integer, primary_key=True)
    app_id = sa.orm.mapped_column(sa.ForeignKey("mastodon_apps.id"), nullable=False)
    user_id = sa.orm.mapped_column(sa.ForeignKey("users.id"), nullable=False)
    access_token = sa.Column(sa.String, nullable=False)
    username = sa.Column(sa.String)

    app = sa.orm.relationship("MastodonApp", lazy='joined')
    user = sa.orm.relationship("User", back_populates='mastodon_accounts')


class Feed(db.Model):
    """
    Represents an external source of items, e.g. an RSS feed or social app account.
    """
    __tablename__ = 'feeds'

    TYPE_RSS = 'rss'
    TYPE_MASTODON_HOME = 'mastodon'
    TYPE_MASTODON_NOTIFICATIONS = 'mastodon_notifications'
    TYPE_CUSTOM = 'custom'

    id = sa.Column(sa.Integer, primary_key=True)
    user_id = sa.orm.mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)

    url = sa.Column(sa.String)
    type = sa.Column(sa.String, nullable=False)

    name = sa.Column(sa.String)
    icon_url = sa.Column(sa.String)

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False,
                        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    last_fetch = sa.Column(sa.TIMESTAMP)

    entries = sa.orm.relationship("Entry", back_populates="feed",
                                  cascade="all, delete-orphan", lazy='dynamic')
    raw_data = sa.orm.deferred(sa.Column(sa.String,
                                         doc="The original feed data received from the feed, as JSON"))

    folder = sa.Column(sa.String, index=True)

    __mapper_args__ = {'polymorphic_on': type,
                       'polymorphic_identity': 'feed'}

    __table_args__ = (sa.UniqueConstraint("user_id", "name"),
                      sa.Index("ix_name_user", "user_id", "name"))

    def __repr__(self):
        return f'<{self.__class__.__name__} {self.name}>'

    @classmethod
    def resolve(cls, type):
        "Return the Feed model subclass for the given feed type."
        subclasses = {
            cls.TYPE_RSS: RssFeed,
            cls.TYPE_MASTODON_HOME: MastodonHomeFeed,
            cls.TYPE_MASTODON_NOTIFICATIONS: MastodonNotificationsFeed,
            cls.TYPE_CUSTOM: CustomFeed
        }

        subcls = subclasses.get(type)
        if not subcls:
            raise ValueError(f'unknown type {type}')
        return subcls

    @hybrid_property
    def is_mastodon(self):
        return (self.type == Feed.TYPE_MASTODON_HOME) | (self.type == Feed.TYPE_MASTODON_NOTIFICATIONS)

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

        cooldown_minutes = datetime.timedelta(minutes=app.config['SKIP_RECENTLY_UPDATED_MINUTES'])
        if not force and self.last_fetch and (utcnow - self.last_fetch < cooldown_minutes):
            app.logger.info('skipping recently synced feed %s', self.name)
            return

        entries = self.fetch_entry_data(force)
        self.last_fetch = utcnow

        for values in entries:
            # upsert to handle already seen entries.
            # updated time set explicitly as defaults are not honored in manual on_conflict_do_update
            values['updated'] = utcnow
            values['feed_id'] = self.id
            values['user_id'] = self.user_id
            db.session.execute(
                sqlite.insert(Entry).
                values(**values).
                on_conflict_do_update(("feed_id", "remote_id"), set_=values)
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
            (sa.func.count(cls.id) / days_since_creation < 20, 4),  # 20 times a day or less
            else_=5  # more
        )

        return db.select(cls.id, rank_func.label('rank'))\
            .join(Entry)\
            .filter(Entry.sort_date >= retention_date)\
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

    filters = sa.Column(sa.String, doc="a comma separated list of conditions that feed source entries need to meet \
                        to be included in the feed.")

    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_RSS}

    @classmethod
    def from_valuelist(cls, _type, name, url, folder, filters):
        return cls(**dict(name=name, url=url, folder=folder, filters=filters))

    def to_valuelist(self):
        return [self.type, self.name, self.url, self.folder, self.filters]

    def fetch_entry_data(self, force=False):
        from flask import current_app as app
        skip_older_than = datetime.datetime.utcnow() - \
            datetime.timedelta(days=app.config['RSS_SKIP_OLDER_THAN_DAYS'])

        feed_data, entries, etag, modified = parsers.rss.fetch(
            self.name, self.url,
            skip_older_than,
            app.config['RSS_MINIMUM_ENTRY_AMOUNT'],
            None if force else self.last_fetch,
            None if force else self.etag,
            None if force else self.modified_header,
            self.filters)

        self.etag = etag
        self.modified_header = modified
        if feed_data:
            self.raw_data = json.dumps(feed_data)
        return entries

    def load_icon(self):
        self.icon_url = parsers.rss.fetch_icon(self.url)


class MastodonHomeFeed(Feed):
    mastodon_account_id = sa.orm.mapped_column(sa.ForeignKey("mastodon_accounts.id"), nullable=True)
    account = sa.orm.relationship("MastodonAccount", lazy='joined')

    @classmethod
    def from_valuelist(cls, _type, name, url, folder, access_token):
        # csv export not supported for mastodon
        raise NotImplementedError

    def to_valuelist(self):
        # csv export not supported for mastodon
        raise NotImplementedError

    def _api_args(self):
        from flask import current_app as app
        latest_entry = self.entries.order_by(Entry.sort_date.desc()).first()

        args = dict(server_url=self.account.app.api_base_url,
                    access_token=self.account.access_token)
        if latest_entry:
            # there's some entry on db, this is not the first time we're syncing
            # get all toots since the last seen one
            args['newer_than'] = latest_entry.remote_id
        else:
            # if there isn't any entry yet, get the "first page" of toots from the timeline
            args['limit'] = app.config['MASTODON_FETCH_LIMIT']
        return args

    def fetch_entry_data(self, _force=False):
        return parsers.mastodon.fetch_toots(**self._api_args())

    def load_icon(self):
        self.icon_url = parsers.mastodon.fetch_account_data(
            self.account.app.api_base_url, self.account.access_token)['avatar']

    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_MASTODON_HOME}


class MastodonNotificationsFeed(MastodonHomeFeed):

    def fetch_entry_data(self, _force=False):
        return parsers.mastodon.fetch_notifications(**self._api_args())

    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_MASTODON_NOTIFICATIONS}


class CustomFeed(Feed):
    __mapper_args__ = {'polymorphic_identity': Feed.TYPE_CUSTOM}

    def fetch_entry_data(self, _force=False):
        return parsers.custom.fetch(self.name, self.url)


class Entry(db.Model):
    """
    Represents an item within a Feed.
    """

    "Sort entries in reverse chronological order."
    ORDER_RECENCY = 'recency'

    "Sort entries based on the post frequency of the parent feed."
    ORDER_FREQUENCY = 'frequency'

    __tablename__ = 'entries'

    id = sa.Column(sa.Integer, primary_key=True)

    feed_id = sa.orm.mapped_column(sa.ForeignKey("feeds.id"))
    user_id = sa.orm.mapped_column(sa.ForeignKey("users.id"), nullable=False, index=True)
    feed = sa.orm.relationship("Feed", back_populates="entries")
    remote_id = sa.Column(sa.String, nullable=False,
                          doc="The identifier of this entry in its source feed.")

    title = sa.Column(sa.String)
    username = sa.Column(sa.String, index=True)
    user_url = sa.Column(sa.String)
    display_name = sa.Column(
        sa.String, doc="For cases, like mastodon, where there's a full display name in addition to username.")

    avatar_url = sa.Column(
        sa.String, doc="The url of the avatar image to be displayed for the entry.")

    content_short = sa.Column(sa.String, doc="The content to be displayed in the feed preview. HTML is supported. \
    For article entries, it would be an excerpt of the full article content.")

    content_full = sa.orm.deferred(sa.Column(
        sa.String, doc="The content to be displayed in the reader, e.g. the cleaned full article HTML."))

    target_url = sa.Column(
        sa.String, doc="The URL to open when accessing the entry at its source. \
        NULL is interpreted as the entry cannot be open at the source.")

    content_url = sa.Column(
        sa.String, doc="The URL to fetch the full entry content from, for reading locally. \
        NULL is interpreted as the entry cannot be read locally.")

    comments_url = sa.Column(
        sa.String, doc="The URL to fetch the full entry content from, for reading locally. \
        NULL is interpreted as the entry cannot be read locally.")

    media_url = sa.Column(sa.String, doc="URL of a media attachement or preview.")

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False,
                        default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    display_date = sa.Column(sa.TIMESTAMP, nullable=False,
                             doc="The date that will displayed as the publication date of the entry. \
                             Typically the publication or creation date informed at the source.")

    sort_date = sa.Column(sa.TIMESTAMP, nullable=False,
                          doc="The date that determines an entry's chronological order. \
                          Typically the updated date informed at the source.")

    viewed = sa.Column(sa.TIMESTAMP, index=True)
    favorited = sa.Column(sa.TIMESTAMP, index=True)
    pinned = sa.Column(sa.TIMESTAMP, index=True)

    sent_to_kindle = sa.Column(sa.TIMESTAMP, index=True)

    raw_data = sa.orm.deferred(sa.Column(sa.String,
                                         doc="The original entry data received from the feed, as JSON"))

    header = sa.Column(
        sa.String, doc="an html line to put above the title, such as 'user reblogged'.")

    icon_url = sa.Column(
        sa.String, doc="To be used for standalone entry avatars or as a fallback when the feed has no icon.")

    __table_args__ = (sa.UniqueConstraint("feed_id", "remote_id"),
                      sa.Index("entry_sort_ts", sort_date.desc()))

    @classmethod
    def from_url(cls, user_id, url):
        "Load an entry for the given article url if it exists, otherwise create a new one."
        entry = db.session.scalar(db.select(cls)
                                  .filter_by(content_url=url, user_id=user_id))

        if not entry:
            values = parsers.html.fetch(url)
            entry = cls(user_id=user_id, **values)
        return entry

    def __repr__(self):
        return f'<Entry {self.feed_id}/{self.remote_id}>'

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
                self.content_full = scraping.extract(self.content_url)['content']
            except Exception as e:
                logger.debug("failed to fetch content %s", e)

    @classmethod
    def _filtered_query(cls, user_id, hide_seen=False, favorited=None,
                        feed_name=None, username=None, folder=None, older_than=None,
                        text=None):
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
                                 cls.content_short.contains(text) |
                                 cls.content_full.contains(text))

        return query

    @classmethod
    def select_pinned(cls, user_id, **kwargs):
        "Return the full list of pinned entries considering the optional filters."
        query = cls._filtered_query(user_id, **kwargs)\
                   .filter(cls.pinned.is_not(None))\
                   .order_by(cls.pinned.desc())

        return db.session.scalars(query).all()

    @classmethod
    def sorted_by(cls, user_id, ordering, start_at, **filters):
        """
        Return a query to filter entries added after the `start_at` datetime,
        sorted according to the specified `ordering` criteria and with optional filters.
        """
        query = cls._filtered_query(user_id, older_than=start_at, **filters)

        if filters.get('favorited'):
            return query.order_by(cls.favorited.desc())

        elif ordering == cls.ORDER_RECENCY:
            # reverse chronological order
            return query.order_by(cls.sort_date.desc())

        elif ordering == cls.ORDER_FREQUENCY:
            # Order entries by least frequent feeds first then reverse-chronologically for entries in the same
            # frequency rank.
            subquery = Feed.frequency_rank_query()

            # exhaust last n hours of all ranks before moving to older stuff
            # if smaller delta, more chances to bury infrequent posts
            # if bigger, more chances to bury recent stuff under old unseen infrequent posts
            recency_bucket_date = datetime.datetime.utcnow() - datetime.timedelta(hours=24)

            # isouter = true so that if a feed with only old stuff is added, entries still show up
            # even without having a freq rank
            return query.join(Feed, isouter=True)\
                        .join(subquery, Feed.id == subquery.c.id, isouter=True)\
                        .order_by(
                            (cls.sort_date >= recency_bucket_date).desc(),
                            subquery.c.rank,
                            cls.sort_date.desc())
        else:
            raise ValueError('unknown ordering %s' % ordering)
