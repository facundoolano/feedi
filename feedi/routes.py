import datetime
import json
import pathlib
import subprocess
import tempfile
from collections import defaultdict

import flask
import sqlalchemy as sa
import stkclient
from favicon.favicon import BeautifulSoup
from flask import current_app as app

import feedi.models as models
import feedi.tasks as tasks
from feedi.models import db
from feedi.sources import rss


# FIXME the feed_name/entries url is inconsistent with the rest
@app.route("/")
@app.route("/folder/<folder>")
@app.route("/feeds/<feed_name>/entries")
@app.route("/users/<username>")
@app.route("/entries/trash", defaults={'deleted': True})
@app.route("/entries/favorites", defaults={'favorited': True})
def entry_list(**filters):
    """
    Generic view to fetch a list of entries. By default renders the home timeline.
    If accessed with a feed name or a pagination timestam, filter the resuls accordingly.
    If the request is an html AJAX request, respond only with the entry list HTML fragment.
    """
    ENTRY_PAGE_SIZE = 20
    next_page = None

    page = flask.request.args.get('page')
    freq_sort = flask.session.get('freq_sort')
    (entries, next_page) = query_entries_page(ENTRY_PAGE_SIZE, freq_sort, page=page, **filters)

    is_htmx = flask.request.headers.get('HX-Request') == 'true'

    if is_htmx:
        # render a single page of the entry list
        return flask.render_template('entry_list_page.html',
                                     entries=entries,
                                     next_page=next_page)

    # render home, including feeds sidebar
    return flask.render_template('entry_list.html',
                                 pinned=models.Entry.select_pinned(**filters),
                                 entries=entries,
                                 next_page=next_page,
                                 filters=filters)


# TODO this requires unit testing, I bet it's full of bugs :P
def query_entries_page(limit, freq_sort, page=None, **kwargs):
    """
    Fetch a page of entries from db, optionally filtered by feed_name, folder or username.
    A specific sorting is applied according to `freq_sort` (strictly chronological or
    least frequent feeds first).
    `limit` and `page` select the page according to the given sorting criteria.
    The next page indicator is returned as the second element of the return tuple
    """

    if freq_sort:
        if page:
            start_at, page = page.split(':')
            page = int(page)
            start_at = datetime.datetime.fromtimestamp(float(start_at))
        else:
            start_at = datetime.datetime.now()
            page = 1

        entries = models.Entry.select_page_by_frequency(limit, start_at, page, **kwargs)

        # the page marker includes the timestamp at which the first page was fetch, so
        # it doesn't become a "sliding window" that would produce duplicate results.
        # FIXME what if there are new entries added between pages (as handled in the other pagination)
        # also this could obviously trip if the ranks are updated in between calls, but well duplicated entries
        # in infinity scroll aren't the end of the world (they could also be filtered out in the frontend)
        next_page = f'{start_at.timestamp()}:{page + 1}'
        return entries, next_page

    else:
        if page:
            page = datetime.datetime.fromtimestamp(float(page))

        entries = models.Entry.select_page_chronologically(limit, page, **kwargs)

        # We don't use regular page numbers, instead timestamps so we don't get repeated
        # results if there were new entries added in the db after the previous page fetch.
        next_page_ts = entries[-1].remote_updated.timestamp() if entries else None
        return entries, next_page_ts


@app.put("/pinned/<int:id>")
@app.put("/folder/<folder>/pinned/<int:id>")
@app.put("/feeds/<feed_name>/entries/pinned/<int:id>")
@app.put("/users/<username>/pinned/<int:id>")
@app.put("/entries/trash/pinned/<int:id>", defaults={'deleted': True})
@app.put("/entries/favorites/pinned/<int:id>", defaults={'favorited': True})
def entry_pin(id, **filters):
    """
    Toggle the pinned status of the given entry and return the new list of pinned
    entries, respecting the url filters.
    """
    entry = db.get_or_404(models.Entry, id)
    entry.pinned = None if entry.pinned else datetime.datetime.now()
    db.session.commit()

    # get the new list of pinned based on filters
    pinned = models.Entry.select_pinned(**filters)

    # FIXME this, together with the template is a patch to prevent the newly rendered pinned list
    # to base their pin links on this route's url.
    # this is a consequence of sending the htmx fragment as part of this specialized url.
    # there should be a better way to handle this
    pin_base_path = flask.request.path.split('/pinned')[0]

    return flask.render_template("entry_list_page.html",
                                 is_pinned_list=True,
                                 pin_base_path=pin_base_path,
                                 entries=pinned)


@app.put("/entries/favorites/<int:id>/")
def entry_favorite(id):
    "Toggle the favorite status of the given entry."
    entry = db.get_or_404(models.Entry, id)
    entry.favorited = None if entry.favorited else datetime.datetime.now()
    db.session.commit()
    return '', 204


@app.put("/entries/thrash/<int:id>/")
def entry_delete(id):
    "Toggle the deleted status of the given entry."
    entry = db.get_or_404(models.Entry, id)
    entry.deleted = None if entry.deleted else datetime.datetime.now()
    db.session.commit()
    return '', 204


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

        return dict(shortcut_feeds=shortcut_feeds, folders=folders, filters={})
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

    # trigger a sync of this feed to fetch its entries on the background
    tasks.sync_rss_feed(feed.name)

    # NOTE it would be better to redirect to the feed itself, but since we load it async
    # we'd have to show a spinner or something and poll until it finishes loading
    # or alternatively hang the response until the feed is processed, neither of which is ideal
    return flask.redirect(flask.url_for('feed_list'))


@app.delete("/feeds/<feed_name>")
def feed_delete(feed_name):
    "Remove a feed and its entries from the database."
    # FIXME this should probably do a "logic" delete and keep stuff around
    # especially considering that it will kill child entries as well
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
            content = extract_article(entry.content_url)['content']
        except Exception as e:
            return flask.render_template("error_message.html", message=f"Error fetching article: {repr(e)}")
    else:
        # this is not ideal for mastodon, but at least doesn't break
        content = entry.body

    # increase the feed views counter
    entry.feed.views += 1
    db.session.commit()

    return flask.render_template("entry_content.html", entry=entry, content=content)


# FIXME experimental route, should give it proper support
@app.get("/entries/preview")
def preview_content():
    url = flask.request.args['url']
    article = extract_article(url)
    # FIXME hacked, should get meta?
    entry = {"content_url": url,
             "title": article['title'],
             "username": article['byline']}

    return flask.render_template("content_preview.html", content=article['content'], entry=entry)


@app.post("/entries/kindle")
def send_to_kindle():
    """
    TODO
    """
    credentials = app.config.get('KINDLE_CREDENTIALS_PATH')
    if not credentials:
        return '', 204

    credentials = pathlib.Path(credentials)
    try:
        credentials.stat
    except FileNotFoundError:
        return '', 204

    with open(credentials) as fp:
        kindle_client = stkclient.Client.load(fp)

    url = flask.request.args['url']
    article = extract_article(url)

    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fp:
        # pass it through bs4 so it's a well-formed html (otherwise kindle will reject it)
        html_content = str(BeautifulSoup(article['content'], 'lxml'))

        fp.write(html_content)
        fp.close()

        serials = [d.device_serial_number for d in kindle_client.get_owned_devices()]
        kindle_client.send_file(pathlib.Path(fp.name), serials,
                                format='html',
                                author=article['byline'],
                                title=article['title'])

        return '', 204


def extract_article(url):
    # The mozilla/readability npm package shows better results at extracting the
    # article content than all the python libraries I've tried... even than the readabilipy
    # one, which is a wrapper of it. so resorting to running a node.js script on a subprocess
    # for parsing the article sadly this adds a dependency to node and a few npm pacakges
    r = subprocess.run(["feedi/extract_article.js", url], capture_output=True, text=True)
    return json.loads(r.stdout)


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


@app.post("/session/<setting>/")
def toggle_hide_media(setting):
    if setting not in ['hide_media', 'freq_sort']:
        flask.abort(400, "Invalid setting")

    flask.session[setting] = not flask.session.get(setting, False)
    return '', 204
