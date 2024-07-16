import datetime

import flask
import sqlalchemy as sa
from flask import current_app as app
from flask_login import current_user, login_required

import feedi.email as email
import feedi.models as models
import feedi.tasks as tasks
from feedi import scraping
from feedi.models import db
from feedi.parsers import mastodon, rss


@app.route("/users/<username>")
@app.route("/favorites", defaults={'favorited': True}, endpoint='favorites')
@app.route("/folder/<folder>")
@app.route("/feeds/<feed_name>/entries")
@app.get("/entries/kindle", defaults={'sent_to_kindle': True}, endpoint='sent_to_kindle')
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
            ('Add to feed', flask.url_for('entry_add', url=term), 'fas fa-download', 'POST'),
            ('View in reader', flask.url_for('entry_add', url=term, redirect=1), 'fas fa-book-reader', 'POST'),
            ('Discover feed', flask.url_for('feed_add', url=term), 'fas fa-rss'),
        ]
        if current_user.kindle_email:
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
        ('Kindle setup', flask.url_for('kindle_add'), 'fas fa-tablet-alt'),
        ('Kindle log', flask.url_for('sent_to_kindle'), 'fas fa-tablet-alt')
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
    if entry.user_id != current_user.id:
        flask.abort(404)

    if entry.pinned:
        entry.pinned = None
    else:
        entry.fetch_content()
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
    if entry.user_id != current_user.id:
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
                                         sa.func.max(models.Entry.sort_date).label('updated'))
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
    "Remove a feed and its entries from the database. Pinned and favorited entries are preserved."
    feed = db.session.scalar(db.select(models.Feed).filter_by(
        name=feed_name, user_id=current_user.id))

    if not feed:
        flask.abort(404, "Feed not found")

    # preserve pinned and favorited by moving them out of the feed before deleting it.
    update = db.update(models.Entry)\
        .where((models.Entry.feed_id == feed.id) & (
            models.Entry.favorited.isnot(None) |
            models.Entry.pinned.isnot(None)))\
        .values(feed_id=None)
    db.session.execute(update)

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


@app.post("/entries/")
@login_required
def entry_add():
    """
    Redirects to the content reader for the article at the given URL, creating a new entry for it
    if there isn't already one.
    """
    # TODO sanitize?
    url = flask.request.args['url']
    redirect = flask.request.args.get('redirect')

    try:
        entry = models.Entry.from_url(current_user.id, url)
    except Exception:
        if redirect:
            return redirect_response(url)
        else:
            return 'failed to parse entry', 500

    db.session.add(entry)
    db.session.commit()

    if redirect:
        return redirect_response(flask.url_for('entry_view', id=entry.id))
    else:
        return '', 204


@app.get("/entries/<int:id>")
@login_required
def entry_view(id):
    """
    Fetch the entry content from the source and display it for reading locally.
    """
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)

    # When requested through htmx (ajax), this page loads layout first, then the content
    # on a separate request. The reason for this is that article fetching is slow, and we
    # don't want the view entry action to freeze the UI without loading indication.
    # Now, the reason for that freezing is that we are using hx-boosting instead of default
    # browser behavior. I don't like it, but I couldn't figure out how to preserve the feed
    # page/scrolling position on back button unless I jump to view content via htmx

    if 'HX-Request' in flask.request.headers and 'content' not in flask.request.args and not entry.content_full:
        # if ajax/htmx just load the empty UI and load content asynchronously
        return flask.render_template("entry_content.html", entry=entry, content=None)
    else:
        if not entry.content_url and not entry.target_url:
            # this view can't work if no entry or content url
            return "Entry not readable", 400

        # if it's a video site, just redirect. TODO add more sites
        if 'youtube.com' in entry.content_url or 'vimeo.com' in entry.content_url:
            return redirect_response(entry.target_url)

        # if full browser load or explicit content request, fetch the article synchronously
        entry.fetch_content()
        if entry.content_full:
            entry.viewed = entry.viewed or datetime.datetime.utcnow()
            db.session.commit()
            return flask.render_template("entry_content.html", entry=entry, content=entry.content_full)

        return redirect_response(entry.target_url)


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


@app.post("/entries/kindle")
@login_required
def send_to_kindle():
    """
    If the user has a registered device, send the article in the given URL through kindle.
    """
    if not current_user.kindle_email:
        return '', 204

    url = flask.request.args['url']

    article = scraping.extract(url)
    attach_data = scraping.package_epub(url, article)
    email.send(current_user.kindle_email, attach_data, filename=article['title'])

    # save as read entry if not already, to keep track of sent to kindle urls
    entry = models.Entry.from_url(current_user.id, url)
    entry.sent_to_kindle = datetime.datetime.now()
    entry.viewed = entry.viewed or datetime.datetime.utcnow()
    entry.content_full = article['content']

    db.session.add(entry)
    db.session.commit()

    return '', 204


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

    if entry.user_id != current_user.id:
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
