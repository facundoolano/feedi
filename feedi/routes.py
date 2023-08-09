import datetime
import urllib
from collections import defaultdict

import flask
import newspaper
import sqlalchemy as sa
from bs4 import BeautifulSoup
from flask import current_app as app

import feedi.models as models
from feedi.models import db
from feedi.sources import rss


@app.route("/")
@app.route("/folder/<folder>")
@app.route("/feeds/<feed_name>/entries")
@app.route("/users/<username>")
def entry_list(feed_name=None, username=None, folder=None):
    """
    Generic view to fetch a list of entries. By default renders the home timeline.
    If accessed with a feed name or a pagination timestam, filter the resuls accordingly.
    If the request is an html AJAX request, respond only with the entry list HTML fragment.
    """
    ENTRY_PAGE_SIZE = 20

    after_ts = flask.request.args.get('after')
    entries = entry_page(limit=ENTRY_PAGE_SIZE, after_ts=after_ts,
                         feed_name=feed_name, username=username, folder=folder)

    is_htmx = flask.request.headers.get('HX-Request') == 'true'

    if is_htmx:
        # render a single page of the entry list
        return flask.render_template('entry_list.html', entries=entries)

    # render home, including feeds sidebar
    return flask.render_template('entries.html', entries=entries,
                                 selected_feed=feed_name,
                                 selected_folder=folder)


# TODO move to db module
def entry_page(limit, after_ts=None, feed_name=None, username=None, folder=None):
    """
    Fetch a page of entries from db, optionally filtered by feed_name.
    The page is selected from entries older than the given date, or the
    most recent ones if no date is given.
    """
    query = db.select(models.Entry)

    if after_ts:
        dt = datetime.datetime.fromtimestamp(float(after_ts))
        query = query.filter(models.Entry.remote_updated < dt)

    if feed_name:
        query = query.filter(models.Entry.feed.has(name=feed_name))

    if folder:
        query = query.filter(models.Entry.feed.has(folder=folder))

    if username:
        query = query.filter(models.Entry.username == username)

    query = query.order_by(models.Entry.remote_updated.desc()).limit(limit)

    return db.session.scalars(query)


@app.context_processor
def sidebar_feeds():
    """
    For regular browser request (i.e. no ajax requests triggered by htmx),
    fetch folders and quick access feeds to make available to any template needing to render the sidebar.
    """
    if flask.request.headers.get('HX-Request') != 'true':
        shortcut_feeds = db.session.scalars(db.select(models.Feed)
                                            .order_by(models.Feed.views.desc())
                                            .limit(5)).all()

        in_folder = db.session.scalars(db.select(models.Feed)
                                       .filter(models.Feed.folder != None, models.Feed.folder != '')
                                       .order_by(models.Feed.views.desc())).all()

        folders = defaultdict(list)
        for feed in in_folder:
            folders[feed.folder].append(feed)

        return dict(shortcut_feeds=shortcut_feeds, folders=folders)
    return {}


@app.route("/feeds")
def feed_list():
    feeds = db.session.scalars(db.select(models.Feed)).all()
    return flask.render_template('feeds.html', feeds=feeds)


@app.get("/feeds/add")
def feed_add():
    return flask.render_template('feed_edit.html')


@app.post("/feeds/add")
def feed_add_submit():
    # Assume we only explicitly create RSS feeds for now. Mastodon would have a login flow, not a form

    # TODO handle errors, eg required fields, duplicate name
    values = dict(**flask.request.form)
    values['icon_url'] = rss.detect_feed_icon(values['url'])
    feed = models.RssFeed(**values)
    db.session.add(feed)
    db.session.commit()

    return flask.redirect(flask.url_for('feed_list'))


@app.delete("/feeds/<feed_name>")
def feed_delete(feed_name):
    "Remove a feed and its entries from the database."
    query = db.delete(models.Feed).where(models.Feed.name == feed_name)
    db.session.execute(query)
    db.session.commit()
    return '', 204


@app.get("/feeds/edit/<feed_name>")
def feed_edit(feed_name):
    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    if not feed:
        flask.abort(404, "Feed not found")

    return flask.render_template('feed_edit.html', feed=feed)


@app.post("/feeds/edit/<feed_name>")
def feed_edit_submit(feed_name):
    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    if not feed:
        flask.abort(404, "Feed not found")

    # setting values at the instance level instead of issuing an update on models.Feed
    # so we don't need to explicitly inspect the feed to figure out its subclass
    for (attr, value) in flask.request.form.items():
        setattr(feed, attr, value)
    db.session.commit()

    return flask.redirect(flask.url_for('feed_list'))


@app.get("/entries/<int:id>/")
def fetch_entry_content(id):
    """
    Fetch the entry content from the source and display it for reading locally.
    """
    entry = db.session.scalar(db.select(models.Entry).filter_by(id=id))

    # FIXME fix error handling in templates
    if not entry:
        return flask.render_template("error_message.html", message="Entry not found")

    if entry.feed.type == models.Feed.TYPE_RSS:
        try:
            content = extract_article(entry.content_url)
        except Exception as e:
            return flask.render_template("error_message.html", message=f"Error fetching article: {repr(e)}")
    else:
        # this is not ideal for mastodon, but at least doesn't break
        content = entry.body

    # increase the feed views counter
    entry.feed.views += 1
    db.session.commit()

    return flask.render_template("entry_content.html", entry=entry, content=content)


@app.route("/feeds/<int:id>/raw")
def raw_feed(id):
    """
    Shows a JSON dump of the feed data as received from the source.
    """
    feed = db.get_or_404(models.Feed, id)

    return app.response_class(
        response=feed.raw_data,
        status=200,
        mimetype='application/json'
    )


@app.route("/entries/<int:id>/raw")
def raw_entry(id):
    """
    Shows a JSON dump of the entry data as received from the source.
    """
    entry = db.get_or_404(models.Entry, id)
    return app.response_class(
        response=entry.raw_data,
        status=200,
        mimetype='application/json'
    )


def extract_article(url):
    """
    Given an article URL, fetch its html and clean it up to its minimal readable content
    (eg no ads, etc)
    """
    # TODO handle case if not html, eg if destination is a pdf
    # TODO to preserve the author data, maybe show the top image

    # https://stackoverflow.com/questions/62943152/shortcomings-of-newspaper3k-how-to-scrape-only-article-html-python
    config = newspaper.Config()
    config.fetch_images = True
    config.request_timeout = 30
    config.keep_article_html = True
    article = newspaper.Article(url, config=config)

    article.download()
    article.parse()

    # TODO unit test this
    # cleanup images from the article html
    soup = BeautifulSoup(article.article_html, 'lxml')
    for img in soup.find_all('img'):
        src = img.get('src')
        if not src:
            # skip images with missing src
            img.decompose()
        elif not urllib.parse.urlparse(src).netloc:
            # fix paths of relative img urls by joining with the main articule url
            img['src'] = urllib.parse.urljoin(url, src)

    return str(soup)


# TODO this could be PUT/DELETE to set/unset, and make it to work generically with any setting
@app.post("/session/hide_media/")
def toggle_hide_media():
    flask.session['hide_media'] = not flask.session.get('hide_media', False)
    return '', 204
