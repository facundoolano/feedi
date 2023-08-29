import datetime
import json
import pathlib
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict

import flask
import requests
import stkclient
from favicon.favicon import BeautifulSoup
from flask import current_app as app

import feedi.models as models
import feedi.tasks as tasks
from feedi.models import db
from feedi.sources import rss


# FIXME the feed_name/entries url is inconsistent with the rest
@app.route("/users/<username>")
@app.route("/entries/trash", defaults={'deleted': True}, endpoint='favorites')
@app.route("/entries/favorites", defaults={'favorited': True}, endpoint='thrash')
@app.route("/folder/<folder>")
@app.route("/feeds/<feed_name>/entries")
@app.route("/")
def entry_list(**filters):
    """
    Generic view to fetch a list of entries. By default renders the home timeline.
    If accessed with a feed name or a pagination timestam, filter the resuls accordingly.
    If the request is an html AJAX request, respond only with the entry list HTML fragment.
    """
    next_page = None

    page = flask.request.args.get('page')
    ordering = flask.session.get('ordering', models.Entry.ORDER_RECENCY)

    text = flask.request.args.get('q', '').strip()
    if text:
        filters = dict(text=text, **filters)

    (entries, next_page) = query_entries_page(ordering, page=page, **filters)

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


def query_entries_page(ordering, page=None, **kwargs):
    """
    Fetch a `page` of entries from db, optionally filtered by feed_name, folder or username.
    and according to the provided `ordering` criteria.
    The return value is a tuple with the page of resulting entries and a string to
    be passed to fetch the next page in a subsequent request.
    """
    ENTRY_PAGE_SIZE = 20

    # pagination includes a start at timestamp so the entry set remains the same
    # even if new entries are added between requests
    if page:
        start_at, page = page.split(':')
        page = int(page)
        start_at = datetime.datetime.fromtimestamp(float(start_at))
    else:
        start_at = datetime.datetime.utcnow()
        page = 1

    next_page = f'{start_at.timestamp()}:{page + 1}'

    query = models.Entry.sorted_by(ordering, start_at, **kwargs)
    entries = db.paginate(query, per_page=ENTRY_PAGE_SIZE, page=page)
    return entries, next_page


@app.get("/autocomplete")
def autocomplete():
    """
    TODO
    """
    term = flask.request.args['q'].strip()

    # TODO add icons for everything
    options = []

    if term.startswith('http://') or term.startswith('https://'):
        # we can reasonably assume this is a url

        options += [
            # FIXME add support for these to feed_add
            ('Add feed', flask.url_for('feed_add', url=term)),
            ('Preview article', flask.url_for('preview_content', url=term)),
            ('Crawl feed', flask.url_for('feed_add', crawl=term)),
        ]
    else:
        options.append(('Search: ' + term, flask.url_for('entry_list', q=term)))

        folders = db.session.scalars(
            db.select(models.Feed.folder).filter(models.Feed.folder.icontains(term)).distinct()
        ).all()
        options += [(f, flask.url_for('entry_list', folder=f)) for f in folders]

        feed_names = db.session.scalars(
            db.select(models.Feed.name).filter(models.Feed.name.icontains(term)).distinct()
        ).all()
        options += [('View ' + f, flask.url_for('entry_list', feed_name=f)) for f in feed_names]
        options += [('Edit ' + f, flask.url_for('feed_edit', feed_name=f)) for f in feed_names]

    static_options = [
        ('Home', flask.url_for('entry_list')),
        ('Favorites', flask.url_for('favorites', favorited=True)),
        ('Thrash', flask.url_for('thrash', deleted=True)),
        ('Manage Feeds', flask.url_for('feed_list'))
    ]
    for so in static_options:
        if term.lower() in so[0].lower():
            options.append(so)

    return flask.render_template("autocomplete.html", options=options)


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
    if entry.pinned:
        entry.pinned = None
    else:
        entry.pinned = datetime.datetime.utcnow()
        entry.feed.score += 2
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
    if entry.favorited:
        entry.favorited = None
    else:
        entry.favorited = datetime.datetime.utcnow()
        entry.feed.score += 2

    db.session.commit()
    return '', 204


@app.put("/entries/thrash/<int:id>/")
def entry_delete(id):
    "Toggle the deleted status of the given entry."
    entry = db.get_or_404(models.Entry, id)

    if entry.deleted:
        entry.deleted = None
    else:
        entry.deleted = datetime.datetime.utcnow()
        entry.feed.score -= 1

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
                                            .order_by(models.Feed.score.desc())
                                            .limit(5)).all()

        in_folder = db.session.scalars(db.select(models.Feed)
                                       .filter(models.Feed.folder != None, models.Feed.folder != '')
                                       .order_by(models.Feed.score.desc())).all()

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

    # increase the feed score counter
    entry.feed.score += 1
    db.session.commit()

    return flask.render_template("entry_content.html", entry=entry, content=content)


# for now this is accesible dragging an url to the searchbox
# later it will be an autocomplete command there
@app.get("/entries/preview")
def preview_content():
    """
    Preview an url content in the reader, as if it was an entry parsed from a feed.
    """
    url = flask.request.args['url']
    article = extract_article(url)
    entry = {"content_url": url,
             "title": article['title'],
             "username": article['byline']}

    return flask.render_template("content_preview.html", content=article['content'], entry=entry)


@app.post("/entries/kindle")
def send_to_kindle():
    """
    If there's a registered device, send the article in the given URL through kindle.
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

    # a tempfile is necessary because the kindle client expects a local filepath to upload
    # the file contents are a zip including the article.html and its image assets
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fp:
        compress_article(fp.name, article)

        serials = [d.device_serial_number for d in kindle_client.get_owned_devices()]
        kindle_client.send_file(pathlib.Path(fp.name), serials,
                                format='zip',
                                author=article['byline'],
                                title=article['title'])

        return '', 204


def compress_article(outfilename, article):
    """
    Extract the article content, convert it to a valid html doc, localize its images and write
    everything as a zip in the given file (which should be open for writing).
    """

    # pass it through bs4 so it's a well-formed html (otherwise kindle will reject it)
    soup = BeautifulSoup(article['content'], 'lxml')

    with zipfile.ZipFile(outfilename, 'w', compression=zipfile.ZIP_DEFLATED) as zip:
        # create a subdir in the zip for image assets
        zip.mkdir('article_files')

        for img in soup.findAll('img'):
            img_url = img['src']
            img_filename = 'article_files/' + img['src'].split('/')[-1]

            # update each img src url to point to the local copy of the file
            img['src'] = img_filename

            # download the image into the zip, inside the files subdir
            with requests.get(img_url, stream=True) as img_src, zip.open(img_filename, mode='w') as img_dest:
                shutil.copyfileobj(img_src.raw, img_dest)

        zip.writestr('article.html', str(soup))


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


# TODO improve this views to accept only valid values
@app.post("/session/<setting>/")
def toggle_setting(setting):
    flask.session[setting] = not flask.session.get(setting, False)
    return '', 204


# TODO improve this views to accept only valid values
@app.put("/session/")
def update_setting():
    for (key, value) in flask.request.form.items():
        flask.session[key] = value

    return '', 204
