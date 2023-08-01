import datetime

import flask
import newspaper
from bs4 import BeautifulSoup
from flask import current_app as app

import feedi.models as models
from feedi.models import db

ENTRY_PAGE_SIZE = 20


@app.route("/")
def home():
    query = db.select(models.Entry).order_by(models.Entry.remote_updated.desc()).limit(ENTRY_PAGE_SIZE)
    entries = [e for (e, ) in db.session.execute(query)]
    return flask.render_template('base.html', entries=entries)


@app.route("/entries/after/<float:ts>/")
def entry_page(ts):
    "Load a page of entries, older than the given timestamp. Used to implement infinite scrolling of the feed."
    dt = datetime.datetime.fromtimestamp(ts)
    query = db.select(models.Entry).filter(models.Entry.remote_updated < dt)\
                                   .order_by(models.Entry.remote_updated.desc()).limit(ENTRY_PAGE_SIZE)
    entries = [e for (e, ) in db.session.execute(query)]
    return flask.render_template('entry_list.html', entries=entries)


@app.route("/feeds/<int:id>/raw")
def raw_feed(id):
    feed = db.get_or_404(models.Feed, id)

    return app.response_class(
        response=feed.raw_data,
        status=200,
        mimetype='application/json'
    )


def error_fragment(msg):
    return flask.render_template("error_message.html", message=msg)


@app.route("/entries/<int:id>/raw")
def raw_entry(id):
    entry = db.get_or_404(models.Entry, id)
    return app.response_class(
        response=entry.raw_data,
        status=200,
        mimetype='application/json'
    )


@app.route("/entries/<int:id>/content/", methods=['GET'])
def fetch_entry_content(id):
    result = db.session.execute(db.select(models.Entry).filter_by(id=id)).first()
    if not result:
        return error_fragment("Entry not found")
    (entry, ) = result

    if entry.feed.type == models.Feed.TYPE_RSS:
        try:
            return extract_article(entry.content_url)
        except Exception as e:
            return error_fragment(f"Error fetching article: {repr(e)}")
    else:
        # this is not ideal for mastodon, but at least doesn't break
        return entry.body


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
