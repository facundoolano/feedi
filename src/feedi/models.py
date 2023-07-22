import sqlalchemy as sa

import feedi.database as db

# TODO consider adding explicit support for url columns


class Feed(db.Base):
    """
    TODO
    """
    __tablename__ = 'feeds'
    id = sa.Column(sa.Integer, primary_key=True)

    name = sa.Column(sa.String)
    url = sa.Column(sa.String)
    icon_url = sa.Column(sa.String)

    # FIXME select from known enums
    parser_type = sa.Column(sa.String)

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f'<Feed {self.name}>'


class Entry(db.Base):
    """
    TODO
    """
    __tablename__ = 'entries'
    __table_args__ = (UniqueConstraint("feed_id", "remote_id"),)

    id = sa.Column(sa.Integer, primary_key=True)

    feed_id = mapped_column(ForeignKey("feeds.id"))
    feed = relationship("Feed", back_populates="entries")
    remote_id = sa.Column(sa.String)

    title = sa.Column(sa.String, nullable=False)
    title_url = sa.Column(sa.String, nullable=False)
    avatar_url = sa.Column(sa.String)
    user_url = sa.Column(sa.String)

    body = sa.Column(sa.String, doc="The content to be displayed in the feed. HTML is supported. For article entries, it would be an excerpt of the full article conent.")
    entry_url = sa.Column(sa.String, doc="The URL of this entry in the source. For link aggregators this would be the comments page.")
    content_url = sa.Column(sa.String, doc="The URL where the full content can be fetched or read. For link aggregators this would be the article redirect url.")
    media_url = sa.Column(sa.String, doc="URL of a media attachement or preview.")

    created = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.utcnow)
    updated = sa.Column(sa.TIMESTAMP, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    remote_created = sa.Column(sa.TIMESTAMP, nullable=False)
    remote_updated = sa.Column(sa.TIMESTAMP)

    def __repr__(self):
        return f'<Entry {self.feed_id}/{self.remote_id}>'
