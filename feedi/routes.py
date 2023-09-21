import datetime
import json
import pathlib
import shutil
import subprocess
import tempfile
import zipfile
from collections import defaultdict

import flask
import stkclient
from bs4 import BeautifulSoup
from flask import current_app as app

import feedi.models as models
import feedi.tasks as tasks
from feedi.models import db
from feedi.requests import requests
from feedi.sources import rss


@app.route("/users/<username>")
@app.route("/favorites", defaults={'favorited': True}, endpoint='favorites')
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

    # already viewed entries should be skipped according to setting
    # but only for views that mix multiple feeds(e.g. home page, folders).
    # If a specific feed is beeing browsed, it makes sense to show all the entries.
    hide_seen_setting = flask.session.get('hide_seen', True)
    is_mixed_feed_view = filters.get('folder') or flask.request.path == '/'
    hide_seen = hide_seen_setting and is_mixed_feed_view

    filters = dict(hide_seen=hide_seen, **filters)
    text = flask.request.args.get('q', '').strip()
    if text:
        filters['text'] = text

    (entries, next_page) = fetch_entries_page(ordering, page=page, **filters)

    if page:
        # if it's a paginated request, render a single page of the entry list
        return flask.render_template('entry_list_page.html',
                                     entries=entries,
                                     filters=filters,
                                     next_page=next_page)

    # render home, including feeds sidebar
    return flask.render_template('entry_list.html',
                                 pinned=models.Entry.select_pinned(**filters),
                                 entries=entries,
                                 next_page=next_page,
                                 is_mixed_feed_view=is_mixed_feed_view,
                                 filters=filters)


def fetch_entries_page(ordering, page=None, **kwargs):
    """
    Fetch a `page` of entries from db, optionally filtered by feed_name, folder or username.
    and according to the provided `ordering` criteria.
    The return value is a tuple with the page of resulting entries and a string to
    be passed to fetch the next page in a subsequent request.

    When pages other than the first are requested, the previous page of entries
    is marked as 'viewed'.
    """
    ENTRY_PAGE_SIZE = 10

    # pagination includes a start at timestamp so the entry set remains the same
    # even if new entries are added between requests
    if page:
        start_at, page = page.split(':')
        page = int(page)
        start_at = datetime.datetime.fromtimestamp(float(start_at))
    else:
        start_at = datetime.datetime.utcnow()
        page = 1

    query = models.Entry.sorted_by(ordering, start_at, **kwargs)
    entries = db.paginate(query, per_page=ENTRY_PAGE_SIZE, page=page)
    next_page = f'{start_at.timestamp()}:{page + 1}' if entries.has_next else None

    if page > 1:
        # mark the previous page as viewed. The rationale is that the user fetches
        # nth page we can assume the previous one can be marked as viewed.
        ids_query = query.with_only_columns(models.Entry.id)
        previous_ids = db.paginate(ids_query, per_page=ENTRY_PAGE_SIZE, page=page - 1).items
        update = db.update(models.Entry)\
            .where(models.Entry.id.in_(previous_ids))\
            .values(viewed=datetime.datetime.utcnow())
        res = db.session.execute(update)
        db.session.commit()

    return entries, next_page


@app.get("/autocomplete")
def autocomplete():
    """
    Given a partial text input in the `q` query arg, render a list of commands matching
    that input, including text search, viewing folders and managing feeds.

    This endpoint is intended to drive the keyboard navigation of the app from the search input.
    """
    term = flask.request.args['q'].strip()

    options = []

    if term.startswith('http://') or term.startswith('https://'):
        # we can reasonably assume this is a url

        options += [
            ('Add feed', flask.url_for('feed_add', url=term), 'fas fa-plus'),
            ('Preview article', flask.url_for('preview_content', url=term), 'far fa-eye'),
            ('Discover feed', flask.url_for('feed_add', discover=term), 'fas fa-rss'),
        ]
    else:
        folders = db.session.scalars(
            db.select(models.Feed.folder).filter(models.Feed.folder.icontains(term)).distinct()
        ).all()
        options += [(f, flask.url_for('entry_list', folder=f), 'far fa-folder-open')
                    for f in folders]

        feed_names = db.session.scalars(
            db.select(models.Feed.name).filter(models.Feed.name.icontains(term)).distinct()
        ).all()
        options += [(f, flask.url_for('entry_list', feed_name=f), 'far fa-list-alt')
                    for f in feed_names]

        # search is less important than quick access but more than edit
        options.append(('Search: ' + term, flask.url_for('entry_list', q=term), 'fas fa-search'))

        options += [('Edit ' + f, flask.url_for('feed_edit', feed_name=f), 'fas fa-edit')
                    for f in feed_names]

    # TODO home and favorites should have more priority than search
    static_options = [
        ('Home', flask.url_for('entry_list'), 'fas fa-home'),
        ('Favorites', flask.url_for('favorites', favorited=True), 'far fa-star'),
        ('Manage Feeds', flask.url_for('feed_list'), 'fas fa-edit')
    ]
    for so in static_options:
        if term.lower() in so[0].lower():
            options.append(so)

    return flask.render_template("autocomplete_items.html", options=options)


@app.put("/pinned/<int:id>")
def entry_pin(id):
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
    filters = dict(**flask.request.args)
    pinned = models.Entry.select_pinned(**filters)

    return flask.render_template("entry_list_page.html",
                                 is_pinned_list=True,
                                 filters=filters,
                                 entries=pinned)


@app.put("/favorites/<int:id>")
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


@app.route("/feeds")
def feed_list():
    feeds = db.session.scalars(db.select(models.Feed)).all()
    return flask.render_template('feeds.html', feeds=feeds)


@app.get("/feeds/new")
def feed_add():
    url = flask.request.args.get('url')
    discover = flask.request.args.get('discover')
    name = None

    if discover:
        result = rss.discover_feed(discover)
        if result:
            (url, name) = result

    return flask.render_template('feed_edit.html',
                                 url=url,
                                 name=name)


@app.post("/feeds/new")
def feed_add_submit():
    # Assume we only explicitly create RSS feeds for now. Mastodon would have a login flow, not a form

    # TODO handle errors, eg required fields, duplicate name
    values = dict(**flask.request.form)

    # FIXME this is hacky
    if values['type'] == models.Feed.TYPE_RSS:
        values['icon_url'] = rss.RSSParser.detect_feed_icon(values['url'])
    else:
        values['icon_url'] = rss.BaseParser.detect_feed_icon(values['url'])

    # TODO use a proper form library instead of this hack
    values['javascript_enabled'] = bool(values.get('javascript_enabled'))

    # FIXME this is hacky
    feed_cls = models.Feed.resolve(values['type'])
    feed = feed_cls(**values)
    db.session.add(feed)
    db.session.commit()

    # trigger a sync of this feed to fetch its entries.
    # making it blocking with .get() so we have entries to show on the redirect
    tasks.sync_feed(feed).get()

    # NOTE it would be better to redirect to the feed itself, but since we load it async
    # we'd have to show a spinner or something and poll until it finishes loading
    # or alternatively hang the response until the feed is processed, neither of which is ideal
    return flask.redirect(flask.url_for('entry_list', feed_name=feed.name))


@app.get("/feeds/<feed_name>")
def feed_edit(feed_name):
    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    if not feed:
        flask.abort(404, "Feed not found")

    return flask.render_template('feed_edit.html', feed=feed)


@app.post("/feeds/<feed_name>")
def feed_edit_submit(feed_name):
    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    if not feed:
        flask.abort(404, "Feed not found")

    # setting values at the instance level instead of issuing an update on models.Feed
    # so we don't need to explicitly inspect the feed to figure out its subclass
    for (attr, value) in flask.request.form.items():
        if attr == 'javascript_enabled':
            # TODO use a proper form library instead of this hack
            value = bool(value)
        setattr(feed, attr, value)
    db.session.commit()

    return flask.redirect(flask.url_for('feed_list'))


@app.delete("/feeds/<feed_name>")
def feed_delete(feed_name):
    "Remove a feed and its entries from the database."
    # FIXME this should probably do a "logic" delete and keep stuff around
    # especially considering that it will kill child entries as well

    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    # running from db.session ensures cascading effects
    db.session.delete(feed)
    db.session.commit()
    return '', 204


@app.post("/feeds/<feed_name>/entries")
def feed_sync(feed_name):
    "Force sync the given feed and redirect to the entry list for it."
    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    if not feed:
        flask.abort(404, "Feed not found")

    task = tasks.sync_feed(feed)
    task.get()

    response = flask.make_response()
    response.headers['HX-Redirect'] = flask.url_for('entry_list', feed_name=feed.name)
    return response


@app.get("/entries/<int:id>")
def entry_view(id):
    """
    Fetch the entry content from the source and display it for reading locally.
    """
    entry = db.get_or_404(models.Entry, id)

    # When requested through htmx (ajax), this page loads layout first, then the content
    # on a separate request. The reason for this is that article fetching is slow, and we
    # don't want the view entry action to freeze the UI without loading indication.
    # Now, the reason for that freezing is that we are using hx-boosting instead of default
    # browser behavior. I don't like it, but I couldn't figure out how to preserve the feed
    # page/scrolling position on back button unless I jump to view content via htmx

    if 'HX-Request' in flask.request.headers and not 'content' in flask.request.args:
        # if ajax/htmx just load the empty UI and load content asynchronously
        content = None
    else:
        # if full browser load or explicit content request, fetch the article synchronously
        content = extract_article(
            entry.content_url, entry.feed.javascript_enabled)['content']
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
    js = 'js' in flask.request.args
    article = extract_article(url, js)
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
    js = 'js' in flask.request.args
    article = extract_article(url, js)

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
        for img in soup.findAll('img'):
            img_url = img['src']
            img_filename = 'article_files/' + img['src'].split('/')[-1].split('?')[0]

            # update each img src url to point to the local copy of the file
            img['src'] = img_filename

            # download the image into the zip, inside the files subdir
            with requests.get(img_url, stream=True) as img_src, zip.open(img_filename, mode='w') as img_dest:
                shutil.copyfileobj(img_src.raw, img_dest)

        zip.writestr('article.html', str(soup))


def extract_article(url, javascript=False):
    # The mozilla/readability npm package shows better results at extracting the
    # article content than all the python libraries I've tried... even than the readabilipy
    # one, which is a wrapper of it. so resorting to running a node.js script on a subprocess
    # for parsing the article sadly this adds a dependency to node and a few npm pacakges
    command = ["feedi/extract_article.js", url]
    if javascript:
        # pass a flag to use a headless browser to fetch the page source
        command += ['--js', '--delay', str(app.config['JS_LOADING_DELAY_MS'])]

    app.logger.info("Running subprocess: %s", ' '.join(command))
    r = subprocess.run(command, capture_output=True, text=True)
    article = json.loads(r.stdout)

    # load lazy images by replacing putting the data-src into src and stripping other attrs
    soup = BeautifulSoup(article['content'], 'lxml')

    LAZY_DATA_ATTRS = ['data-src', 'data-lazy-src', 'data-td-src-property', 'data-srcset']
    for data_attr in LAZY_DATA_ATTRS:
        for img in soup.findAll('img', attrs={data_attr: True}):
            img.attrs = {'src': img[data_attr]}

    article['content'] = str(soup)

    return article


@app.route("/feeds/<feed_name>/debug")
def raw_feed(feed_name):
    """
    Shows a JSON dump of the feed data as received from the source.
    """
    feed = db.session.scalar(db.select(models.Feed).filter_by(name=feed_name))
    if not feed:
        flask.abort(404, "Feed not found")

    return app.response_class(
        response=feed.raw_data,
        status=200,
        mimetype='application/json'
    )


@app.route("/entries/<int:id>/debug")
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
@app.post("/session")
def update_setting():
    for (key, value) in flask.request.form.items():
        flask.session[key] = value

    return '', 204


# TODO improve this views to accept only valid values
@app.post("/session/<setting>")
def toggle_setting(setting):
    flask.session[setting] = not flask.session.get(setting, False)
    return '', 204


@app.context_processor
def sidebar_feeds():
    """
    For regular browser request (i.e. no ajax requests triggered by htmx),
    fetch folders and quick access feeds to make available to any template needing to render the sidebar.
    """
    shortcut_feeds = db.session.scalars(db.select(models.Feed)
                                        .order_by(models.Feed.score.desc())
                                        .limit(5)).all()

    in_folder = db.session.scalars(db.select(models.Feed)
                                   .filter(models.Feed.folder != None, models.Feed.folder != '')
                                   .order_by(models.Feed.score.desc())).all()

    folders = defaultdict(list)
    for feed in in_folder:
        if len(folders[feed.folder]) < 5:
            folders[feed.folder].append(feed)

    return dict(shortcut_feeds=shortcut_feeds, folders=folders, filters={})
