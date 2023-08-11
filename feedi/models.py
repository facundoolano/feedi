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
        # registers a custom function that can be used during queries
        # in this case to sort the feed based on the post frequency of the sources
        dbapi_connection.create_function('freq_bucket', 1, Feed.freq_bucket)

    db.create_all()


class Feed(db.Model):
    """
    TODO
    """
    __tablename__ = 'feeds'

    TYPE_RSS = 'rss'
    TYPE_MASTODON_ACCOUNT = 'mastodon'

    id = sa.Column(sa.Integer, primary_key=True)
    type = sa.Column(sa.String, nullable=False)

    name = sa.Column(sa.String, unique=True, index=True)
    icon_url = sa.Column(sa.String)

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

    entries = sa.orm.relationship("Entry", back_populates="feed", cascade="all, delete-orphan", lazy='dynamic')
    raw_data = sa.Column(sa.String, doc="The original feed data received from the feed, as JSON")
    folder = sa.Column(sa.String, index=True)
    views = sa.Column(sa.Integer, default=0, nullable=False,
                      doc="counts how many times articles of this feed have been read. ")

    __mapper_args__ = {'polymorphic_on': type,
                       'polymorphic_identity': 'feed'}

    def __repr__(self):
        return f'<Feed {self.name}>'

    @staticmethod
    def freq_bucket(count):
        """
        To be used as a DB function, this returns a "rank" of the feed based on how
        many posts we've seen (assuming the count is for the last 2 weeks).
        This rank classifies the feeds so the least frequent posters are displayed more
        prominently.
        """
        # this is pretty hacky but it's low effort and servers for experimentation
        if count <= 2:
            # weekly or less
            rank = 1
        elif count < 5:
            # couple of times a week
            rank = 2
        elif count < 15:
            # up to once a day
            rank = 3
        elif count < 45:
            # up to 3 times a day
            rank = 3
        else:
            # more
            rank = 5
        return rank



class RssFeed(Feed):
    url = sa.Column(sa.String)
    last_fetch = sa.Column(sa.TIMESTAMP)
    etag = sa.Column(sa.String, doc="Etag received on last parsed rss, to prevent re-fetching if it hasn't changed.")
    modified_header = sa.Column(sa.String, doc="Last-modified received on last parsed rss, to prevent re-fetching if it hasn't changed.")

    __mapper_args__ = {'polymorphic_identity': 'rss'}


class MastodonAccount(Feed):
    # TODO this could be a fk to a separate table with client/secret
    # to share the feedi app across accounts of that same server
    server_url = sa.Column(sa.String)
    access_token = sa.Column(sa.String)

    __mapper_args__ = {'polymorphic_identity': 'mastodon'}


class Entry(db.Model):
    """
    TODO
    """
    __tablename__ = 'entries'

    id = sa.Column(sa.Integer, primary_key=True)

    feed_id = sa.orm.mapped_column(sa.ForeignKey("feeds.id"))
    feed = sa.orm.relationship("Feed", back_populates="entries")
    remote_id = sa.Column(sa.String, nullable=False, doc="The identifier of this entry in its source feed.")

    title = sa.Column(sa.String, nullable=False)
    username = sa.Column(sa.String, index=True)
    user_url = sa.Column(sa.String, doc="The url of the user that authored the entry.")
    avatar_url = sa.Column(sa.String, doc="The url of the avatar image to be displayed for the entry.")

    body = sa.Column(sa.String, doc="The content to be displayed in the feed preview. HTML is supported. For article entries, it would be an excerpt of the full article content.")
    entry_url = sa.Column(sa.String, doc="The URL of this entry in the source. For link aggregators this would be the comments page.")
    content_url = sa.Column(sa.String, doc="The URL where the full content can be fetched or read. For link aggregators this would be the article redirect url.")
    media_url = sa.Column(sa.String, doc="URL of a media attachement or preview.")

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)
    remote_created = sa.Column(sa.TIMESTAMP, nullable=False)
    remote_updated = sa.Column(sa.TIMESTAMP, nullable=False)

    raw_data = sa.Column(sa.String, doc="The original entry data received from the feed, as JSON")

    # mastodon specific
    reblogged_by = sa.Column(sa.String)

    __table_args__ = (sa.UniqueConstraint("feed_id", "remote_id"),
                      sa.Index("entry_updated_ts", remote_updated.desc()))

    def __repr__(self):
        return f'<Entry {self.feed_id}/{self.remote_id}>'
