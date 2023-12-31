import datetime
import json
import pathlib
import shutil
import subprocess
import tempfile
import zipfile

import flask
import sqlalchemy as sa
from bs4 import BeautifulSoup
from flask import current_app as app
from flask_login import current_user, login_required

import feedi.models as models
import feedi.tasks as tasks
from feedi.models import db
from feedi.parsers import mastodon, rss
from feedi.requests import requests


@app.route("/users/<username>")
@app.route("/favorites", defaults={'favorited': True}, endpoint='favorites')
@app.route("/folder/<folder>")
@app.route("/feeds/<feed_name>/entries")
@app.route("/")
@login_required
def entry_list(**filters):
    """
    Generic view to fetch a list of entries. By default renders the home timeline.
    If accessed with a feed name or a pagination timestam, filter the resuls accordingly.
    If the request is an html AJAX request, respond only with the entry list HTML fragment.
    """
    page = flask.request.args.get('page')
    hide_seen = flask.session.get('hide_seen', True)
    ordering = flask.session.get('ordering', models.Entry.ORDER_FREQUENCY)

    filters = dict(**filters)
    text = flask.request.args.get('q', '').strip()
    if text:
        filters['text'] = text

    is_mixed_feed_list = filters.get('folder') or (
        flask.request.path == '/' and not filters.get('text'))

    (entries, next_page) = fetch_entries_page(page, current_user.id, ordering, hide_seen, is_mixed_feed_list,
                                              **filters)

    if page:
        # if it's a paginated request, render a single page of the entry list
        return flask.render_template('entry_list_page.html',
                                     entries=entries,
                                     filters=filters,
                                     next_page=next_page)

    # render home, including feeds sidebar
    return flask.render_template('entry_list.html',
                                 pinned=models.Entry.select_pinned(current_user.id, **filters),
                                 entries=entries,
                                 next_page=next_page,
                                 is_mixed_feed_view=is_mixed_feed_list,
                                 filters=filters)


def fetch_entries_page(page_arg,
                       user_id,
                       ordering_setting,
                       hide_seen_setting,
                       is_mixed_feed_list, **filters):
    """
    Fetch a page of entries from db, optionally applying query filters (text search, feed, folder, etc.).
    The entry ordering depends on current filters and user session settings.
    The return value is a tuple with the page of resulting entries and a string to
    be passed to fetch the next page in a subsequent request.

    When pages other than the first are requested, the previous page of entries
    is marked as 'viewed'.
    """
    # already viewed entries should be skipped according to setting
    # but only for views that mix multiple feeds(e.g. home page, folders).
    # If a specific feed is beeing browsed, it makes sense to show all the entries.
    filters['hide_seen'] = is_mixed_feed_list and hide_seen_setting

    # we only want to try a special sorting when looking at a folder or the home timeline
    # so, for instance, we get old entries when looking at a specific feeds
    ordering = ordering_setting if is_mixed_feed_list else models.Entry.ORDER_RECENCY

    # pagination includes a start at timestamp so the entry set remains the same
    # even if new entries are added between requests
    if page_arg:
        start_at, page_num = page_arg.split(':')
        page_num = int(page_num)
        start_at = datetime.datetime.fromtimestamp(float(start_at))
    else:
        start_at = datetime.datetime.utcnow()
        page_num = 1

    query = models.Entry.sorted_by(user_id, ordering, start_at, **filters)
    entry_page = db.paginate(query, per_page=app.config['ENTRY_PAGE_SIZE'], page=page_num)
    next_page = f'{start_at.timestamp()}:{page_num + 1}' if entry_page.has_next else None

    if entry_page.has_prev:
        # mark the previous page as viewed. The rationale is that the user fetches
        # nth page we can assume the previous one can be marked as viewed.
        previous_ids = [e.id for e in entry_page.prev().items]
        update = db.update(models.Entry)\
            .where(models.Entry.id.in_(previous_ids))\
            .values(viewed=datetime.datetime.utcnow())
        db.session.execute(update)
        db.session.commit()

    return entry_page, next_page


@app.get("/autocomplete")
@login_required
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
            ('Preview article', flask.url_for('preview_content', url=term), 'far fa-eye'),
            ('Discover feed', flask.url_for('feed_add', url=term), 'fas fa-rss'),
        ]
        if current_user.has_kindle:
            options += [('Send to Kindle',
                         flask.url_for('send_to_kindle', url=term), 'fas fa-tablet-alt',
                         'POST')]
    else:
        folders = db.session.scalars(
            db.select(models.Feed.folder)
            .filter(models.Feed.folder.icontains(term),
                    models.Feed.user_id == current_user.id).distinct()
        ).all()
        options += [(f, flask.url_for('entry_list', folder=f), 'far fa-folder')
                    for f in folders]

        feed_names = db.session.scalars(
            db.select(models.Feed.name)
            .filter(models.Feed.name.icontains(term),
                    models.Feed.user_id == current_user.id
                    ).distinct()
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
        ('Add Feed', flask.url_for('feed_add'), 'fas fa-plus'),
        ('Manage Feeds', flask.url_for('feed_list'), 'fas fa-edit'),
        ('Mastodon login', flask.url_for('mastodon_oauth'), 'fab fa-mastodon'),
        ('Kindle setup', flask.url_for('kindle_add'), 'fas fa-tablet-alt')
    ]
    for so in static_options:
        if term.lower() in so[0].lower():
            options.append(so)

    return flask.render_template("autocomplete_items.html", options=options)


@app.put("/pinned/<int:id>")
@login_required
def entry_pin(id):
    """
    Toggle the pinned status of the given entry and return the new list of pinned
    entries, respecting the url filters.
    """
    entry = db.get_or_404(models.Entry, id)
    if entry.feed.user_id != current_user.id:
        flask.abort(404)

    if entry.pinned:
        entry.pinned = None
    else:
        entry.pinned = datetime.datetime.utcnow()
    db.session.commit()

    # get the new list of pinned based on filters
    filters = dict(**flask.request.args)
    pinned = models.Entry.select_pinned(current_user.id, **filters)

    return flask.render_template("entry_list_page.html",
                                 is_pinned_list=True,
                                 filters=filters,
                                 entries=pinned)


@app.put("/favorites/<int:id>")
@login_required
def entry_favorite(id):
    "Toggle the favorite status of the given entry."
    entry = db.get_or_404(models.Entry, id)
    if entry.feed.user_id != current_user.id:
        flask.abort(404)

    if entry.favorited:
        entry.favorited = None
    else:
        entry.favorited = datetime.datetime.utcnow()

    db.session.commit()
    return '', 204


@app.put("/mastodon/favorites/<int:id>")
@login_required
def mastodon_favorite(id):
    entry = db.get_or_404(models.Entry, id)
    if entry.feed.user_id != current_user.id:
        flask.abort(404)

    if not entry.feed.is_mastodon:
        flask.abort(400)

    masto_acct = entry.feed.account
    mastodon.favorite(masto_acct.app.api_base_url,
                      masto_acct.access_token,
                      entry.remote_id)
    return '', 204


@app.put("/mastodon/boosts/<int:id>")
@login_required
def mastodon_boost(id):
    entry = db.get_or_404(models.Entry, id)
    if entry.feed.user_id != current_user.id:
        flask.abort(404)

    if not entry.feed.is_mastodon:
        flask.abort(400)

    masto_acct = entry.feed.account
    mastodon.boost(masto_acct.app.api_base_url,
                   masto_acct.access_token,
                   entry.remote_id)

    return '', 204


@app.route("/feeds")
@login_required
def feed_list():
    subquery = models.Feed.frequency_rank_query()
    feeds = db.session.execute(db.select(models.Feed, subquery.c.rank, sa.func.count(1),
                                         sa.func.max(models.Entry.remote_updated).label('updated'))
                               .filter(models.Feed.user_id == current_user.id)
                               .join(subquery, models.Feed.id == subquery.c.id, isouter=True)
                               .join(models.Entry, models.Feed.id == models.Entry.feed_id, isouter=True)
                               .group_by(models.Feed)
                               .order_by(sa.text('rank desc'), sa.text('updated desc')))

    return flask.render_template('feeds.html', feeds=feeds)


@app.get("/feeds/new")
@login_required
def feed_add():
    url = flask.request.args.get('url')
    name = None
    error_msg = None

    if url:
        result = rss.discover_feed(url)
        if result:
            (url, name) = result

        if not result or not url:
            error_msg = "RSS/Atom feed link not found at the given URL."

    folders = db.session.scalars(
        db.select(models.Feed.folder)
        .filter(models.Feed.folder.isnot(None),
                models.Feed.folder.isnot(''))
        .filter_by(user_id=current_user.id).distinct())

    return flask.render_template('feed_edit.html',
                                 url=url,
                                 name=name,
                                 folders=folders,
                                 error_msg=error_msg)


@app.post("/feeds/new")
@login_required
def feed_add_submit():
    # FIXME use a forms lib for validations, type coercion, etc
    values = {k: v.strip() for k, v in flask.request.form.items() if v}

    if not values.get('name'):
        return flask.render_template('feed_edit.html', error_msg='name is required', **values)

    if not values.get('url') and not values.get('type', '').startswith('mastodon'):
        return flask.render_template('feed_edit.html', error_msg='url is required', **values)

    name = values.get('name')
    feed = db.session.scalar(db.select(models.Feed).filter_by(
        name=name, user_id=current_user.id))
    if feed:
        return flask.render_template('feed_edit.html', error_msg=f"A feed with name '{name}' already exists", **values)

    feed_cls = models.Feed.resolve(values['type'])

    # FIXME this is an ugly patch
    if not values['type'].startswith('mastodon') and values.get('mastodon_account_id'):
        del values['mastodon_account_id']

    feed = feed_cls(**values)
    feed.user_id = current_user.id
    db.session.add(feed)
    db.session.flush()

    feed.load_icon()
    db.session.commit()

    # trigger a sync of this feed to fetch its entries.
    # making it blocking with .get() so we have entries to show on the redirect
    tasks.sync_feed(feed.id, feed.name).get()

    # NOTE it would be better to redirect to the feed itself, but since we load it async
    # we'd have to show a spinner or something and poll until it finishes loading
    # or alternatively hang the response until the feed is processed, neither of which is ideal
    return flask.redirect(flask.url_for('entry_list', feed_name=feed.name))


@app.get("/feeds/<feed_name>")
@login_required
def feed_edit(feed_name):
    feed = db.session.scalar(db.select(models.Feed).filter_by(
        name=feed_name, user_id=current_user.id))
    if not feed:
        flask.abort(404, "Feed not found")

    folders = db.session.scalars(
        db.select(models.Feed.folder)
        .filter(models.Feed.folder.isnot(None),
                models.Feed.folder.isnot(''))
        .filter_by(user_id=current_user.id).distinct()).all()

    return flask.render_template('feed_edit.html', feed=feed, folders=folders)


@app.post("/feeds/<feed_name>")
@login_required
def feed_edit_submit(feed_name):
    feed = db.session.scalar(db.select(models.Feed).filter_by(
        name=feed_name, user_id=current_user.id))
    if not feed:
        flask.abort(404, "Feed not found")

    # FIXME fixme use proper form validations
    values = flask.request.form
    if not values.get('name') or not values.get('url'):
        return flask.render_template('feed_edit.html', error_msg='Name and url are required fields', **values)

    # setting values at the instance level instead of issuing an update on models.Feed
    # so we don't need to explicitly inspect the feed to figure out its subclass
    for (attr, value) in values.items():
        setattr(feed, attr, value.strip())
    db.session.commit()

    return flask.redirect(flask.url_for('feed_list'))


@app.delete("/feeds/<feed_name>")
@login_required
def feed_delete(feed_name):
    "Remove a feed and its entries from the database."
    # FIXME this should probably do a "logic" delete and keep stuff around
    # especially considering that it will kill child entries as well

    feed = db.session.scalar(db.select(models.Feed).filter_by(
        name=feed_name, user_id=current_user.id))
    # running from db.session ensures cascading effects
    db.session.delete(feed)
    db.session.commit()
    return '', 204


@app.post("/feeds/<feed_name>/entries")
@login_required
def feed_sync(feed_name):
    "Force sync the given feed and redirect to the entry list for it."
    feed = db.session.scalar(db.select(models.Feed).filter_by(
        name=feed_name, user_id=current_user.id))
    if not feed:
        flask.abort(404, "Feed not found")

    task = tasks.sync_feed(feed.id, feed.name, force=True)
    task.get()

    response = flask.make_response()
    response.headers['HX-Redirect'] = flask.url_for('entry_list', feed_name=feed.name)
    return response


# TODO unit test this view
@app.get("/entries/<int:id>")
@login_required
def entry_view(id):
    """
    Fetch the entry content from the source and display it for reading locally.
    """
    entry = db.get_or_404(models.Entry, id)
    if entry.feed.user_id != current_user.id:
        flask.abort(404)

    # When requested through htmx (ajax), this page loads layout first, then the content
    # on a separate request. The reason for this is that article fetching is slow, and we
    # don't want the view entry action to freeze the UI without loading indication.
    # Now, the reason for that freezing is that we are using hx-boosting instead of default
    # browser behavior. I don't like it, but I couldn't figure out how to preserve the feed
    # page/scrolling position on back button unless I jump to view content via htmx

    if 'HX-Request' in flask.request.headers and 'content' not in flask.request.args:
        # if ajax/htmx just load the empty UI and load content asynchronously
        return flask.render_template("entry_content.html", entry=entry, content=None)
    else:
        dest_url = entry.content_url or entry.entry_url
        if not dest_url:
            # this view can't work if no entry or content url
            return "Entry not readable", 400

        # if it's a video site, just redirect. TODO add more sites
        if 'youtube.com' in dest_url or 'vimeo.com' in dest_url:
            return redirect_response(dest_url)

        # if full browser load or explicit content request, fetch the article synchronously
        try:
            content = extract_article(dest_url, local_links=True)['content']
            return flask.render_template("entry_content.html", entry=entry, content=content)
        except Exception:
            pass

        return redirect_response(dest_url)


def redirect_response(url):
    """
    Issue the proper redirect depending on whether the current request came
    is a regular one or an ajax/htmx one.
    """
    if 'HX-Request' in flask.request.headers:
        response = flask.make_response()
        response.headers['HX-Redirect'] = url
        return response
    else:
        return flask.redirect(url)


# for now this is accesible dragging an url to the searchbox
# later it will be an autocomplete command there
@app.get("/entries/preview")
@login_required
def preview_content():
    """
    Preview an url content in the reader, as if it was an entry parsed from a feed.
    """
    url = flask.request.args['url']
    try:
        article = extract_article(url, local_links=True)
    except Exception:
        return flask.redirect(url)

    # put together entry stub for the template
    entry = models.Entry(content_url=url,
                         title=article['title'],
                         username=article['byline'])
    return flask.render_template("content_preview.html", content=article['content'], entry=entry)


@app.post("/entries/kindle")
@login_required
def send_to_kindle():
    """
    If the user has a registered device, send the article in the given URL through kindle.
    """
    if not current_user.has_kindle:
        return '', 204

    kindle = db.session.scalar(db.select(models.KindleDevice).filter_by(
        user_id=current_user.id))

    url = flask.request.args['url']
    article = extract_article(url)

    # a tempfile is necessary because the kindle client expects a local filepath to upload
    # the file contents are a zip including the article.html and its image assets
    with tempfile.NamedTemporaryFile(mode='w+', delete=False) as fp:
        compress_article(fp.name, article)

        kindle.send(pathlib.Path(fp.name),
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

            # TODO webp images aren't supported, convert to png or jpg

            # download the image into the zip, inside the files subdir
            with requests.get(img_url, stream=True) as img_src, zip.open(img_filename, mode='w') as img_dest:
                shutil.copyfileobj(img_src.raw, img_dest)

        zip.writestr('article.html', str(soup))


def extract_article(url, local_links=False):
    # The mozilla/readability npm package shows better results at extracting the
    # article content than all the python libraries I've tried... even than the readabilipy
    # one, which is a wrapper of it. so resorting to running a node.js script on a subprocess
    # for parsing the article sadly this adds a dependency to node and a few npm pacakges
    r = subprocess.run(["feedi/extract_article.js", url],
                       capture_output=True, text=True, check=True)

    article = json.loads(r.stdout)

    # load lazy images by replacing putting the data-src into src and stripping other attrs
    soup = BeautifulSoup(article['content'], 'lxml')

    LAZY_DATA_ATTRS = ['data-src', 'data-lazy-src', 'data-td-src-property', 'data-srcset']
    for data_attr in LAZY_DATA_ATTRS:
        for img in soup.findAll('img', attrs={data_attr: True}):
            img.attrs = {'src': img[data_attr]}

    # prevent video iframes to force dimensions
    for iframe in soup.findAll('iframe', height=True):
        del iframe['height']

    if local_links:
        for a in soup.findAll('a', href=True):
            a['href'] = flask.url_for('preview_content', url=a['href'])
            del a['target']

    article['content'] = str(soup)

    return article


@app.route("/feeds/<feed_name>/debug")
@login_required
def raw_feed(feed_name):
    """
    Shows a JSON dump of the feed data as received from the source.
    """
    feed = db.session.scalar(
        db.select(models.Feed)
        .filter_by(name=feed_name, user_id=current_user.id)
        .options(sa.orm.undefer(models.Feed.raw_data))
    )
    if not feed:
        flask.abort(404, "Feed not found")

    return app.response_class(
        response=feed.raw_data,
        status=200,
        mimetype='application/json'
    )


@app.route("/entries/<int:id>/debug")
@login_required
def raw_entry(id):
    """
    Shows a JSON dump of the entry data as received from the source.
    """
    entry = db.get_or_404(models.Entry, id,
                          options=[sa.orm.undefer(models.Entry.raw_data)])

    if entry.feed.user_id != current_user.id:
        flask.abort(404)

    return app.response_class(
        response=entry.raw_data,
        status=200,
        mimetype='application/json'
    )


# TODO improve this views to accept only valid values
@app.put("/session/<setting>/<value>")
@login_required
def update_setting(setting, value):
    flask.session[setting] = value

    return '', 204


# TODO improve this views to accept only valid values
# also the default is dubious
@app.post("/session/<setting>")
@login_required
def toggle_setting(setting):
    flask.session[setting] = not flask.session.get(setting, True)
    return '', 204


# TODO rename to shortcut_folders
@app.context_processor
def sidebar_feeds():
    """
    Fetch folders to make available to any template needing to render the sidebar.
    """
    if current_user.is_authenticated:
        folders = db.session.scalars(db.select(models.Feed.folder)
                                     .filter_by(user_id=current_user.id)
                                     .filter(models.Feed.folder.isnot(None),
                                             models.Feed.folder.isnot(''))
                                     .group_by(models.Feed.folder)
                                     .order_by(sa.func.count(models.Feed.folder).desc())).all()

        return dict(shortcut_folders=folders, filters={})

    return {}
