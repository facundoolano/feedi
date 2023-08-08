import datetime
import urllib

import flask
import newspaper
import sqlalchemy as sa
from bs4 import BeautifulSoup
from flask import current_app as app

import feedi.models as models
from feedi.models import db


@app.route("/")
@app.route("/feeds/<feed_name>")
@app.route("/users/<username>")
def entry_list(feed_name=None, username=None):
    """
    Generic view to fetch a list of entries. By default renders the home timeline.
    If accessed with a feed name or a pagination timestam, filter the resuls accordingly.
    If the request is an html AJAX request, respond only with the entry list HTML fragment.
    """
    ENTRY_PAGE_SIZE = 20

    after_ts = flask.request.args.get('after')
    entries = entry_page(limit=ENTRY_PAGE_SIZE, after_ts=after_ts,
                         feed_name=feed_name, username=username)

    is_htmx = flask.request.headers.get('HX-Request') == 'true'

    if is_htmx:
        # render a single page of the entry list
        return flask.render_template('entry_list.html', entries=entries)

    # render home, including feeds sidebar
    return flask.render_template('entries.html', entries=entries,
                                 shortcut_feeds=shortcut_feeds(),
                                 selected_feed=feed_name)

# TODO move to db module
def entry_page(limit, after_ts=None, feed_name=None, username=None):
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

    if username:
        query = query.filter(models.Entry.username == username)

    query = query.order_by(models.Entry.remote_updated.desc()).limit(limit)

    return [e for (e, ) in db.session.execute(query)]


# FIXME having to call this from several views suggests that I need more smarts either in the
# templates or in the view support functions
def shortcut_feeds():
    # get the 5 feeds with most posts in the last 24 hours
    yesterday = datetime.datetime.now() - datetime.timedelta(hours=24)
    feeds = db.session.execute(db.select(models.Feed)
                               .join(models.Entry)
                               .group_by(models.Feed)
                               .filter(models.Entry.remote_updated > yesterday)
                               .order_by(sa.func.count().desc())
                               .limit(5)).all()
    return [feed for (feed,) in feeds]


@app.route("/feeds")
def feeds():
    feeds = db.session.execute(db.select(models.Feed)).all()
    feeds= [f for (f, ) in feeds]
    return flask.render_template('feeds.html',
                                 feeds=feeds,
                                 shortcut_feeds=shortcut_feeds())


@app.route("/feeds/<int:id>/raw")
def raw_feed(id):
    feed = db.get_or_404(models.Feed, id)

    return app.response_class(
        response=feed.raw_data,
        status=200,
        mimetype='application/json'
    )


@app.route("/entries/<int:id>/", methods=['GET'])
def fetch_entry_content(id):
    result = db.session.execute(db.select(models.Entry).filter_by(id=id)).first()

    # FIXME fix error handling in templates
    if not result:
        return flask.render_template("error_message.html", message="Entry not found")
    (entry, ) = result

    if entry.feed.type == models.Feed.TYPE_RSS:
        try:
            content = extract_article(entry.content_url)
        except Exception as e:
            return flask.render_template("error_message.html", message=f"Error fetching article: {repr(e)}")
    else:
        # this is not ideal for mastodon, but at least doesn't break
        content = entry.body

    return flask.render_template("entry_content.html", entry=entry, content=content,
                                 shortcut_feeds=shortcut_feeds())


@app.route("/entries/<int:id>/raw")
def raw_entry(id):
    entry = db.get_or_404(models.Entry, id)
    return app.response_class(
        response=entry.raw_data,
        status=200,
        mimetype='application/json'
    )

def extract_article(url):
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
        print(src, urllib.parse.urlparse(src).netloc)
        if not src:
            # skip images with missing src
            img.decompose()
        elif not urllib.parse.urlparse(src).netloc:
            # fix paths of relative img urls by joining with the main articule url
            img['src'] = urllib.parse.urljoin(url, src)

    return str(soup)


@app.route("/session/hide_media/", methods=['POST'])
def toggle_hide_media():
    flask.session['hide_media'] = not flask.session.get('hide_media', False)
    return '', 204
