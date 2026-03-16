import datetime
import urllib

import flask
import flask_login
import sqlalchemy as sa
from bs4 import BeautifulSoup
from flask import current_app as app
from flask_login import current_user, login_required
from wtforms import Form, StringField
from wtforms.validators import URL, InputRequired, Optional

import feedi.models as models
from feedi.models import db
from feedi.parsers.requests import requests
from feedi.services import entries, feeds


@app.route("/users/<username>")
@app.route("/favorites", defaults={"favorited": True}, endpoint="favorites")
@app.route("/folder/<folder>")
@app.route("/feeds/<feed_id>/entries")
@app.get("/entries/kindle", defaults={"sent_to_kindle": True}, endpoint="sent_to_kindle")
@app.route("/")
@login_required
def entry_list(**filters):
    """
    Generic view to fetch a list of entries. By default renders the home timeline.
    If accessed with a feed name or a pagination timestamp, filter the results accordingly.
    If the request is an html AJAX request, respond only with the entry list HTML fragment.
    """
    page = flask.request.args.get("page")
    hide_seen = flask.session.get("hide_seen", True)

    filters = dict(**filters)
    text = flask.request.args.get("q", "").strip()
    if text:
        filters["text"] = text

    is_mixed_feed_list = filters.get("folder") or (flask.request.path == "/" and not filters.get("text"))

    entry_page, next_page = entries.fetch_page(current_user.id, page, hide_seen, is_mixed_feed_list, **filters)

    if page:
        # if it's a paginated request, render a single page of the entry list
        return flask.render_template("entry_list_page.html", entries=entry_page, filters=filters, next_page=next_page)

    # render home, including feeds sidebar
    return flask.render_template(
        "entry_list.html",
        pinned=models.Entry.select_pinned(current_user.id, **filters),
        entries=entry_page,
        next_page=next_page,
        is_mixed_feed_view=is_mixed_feed_list,
        filters=filters,
    )


@app.get("/autocomplete")
@login_required
def autocomplete():
    """
    Given a partial text input in the `q` query arg, render a list of commands matching
    that input, including text search, viewing folders and managing feeds.

    This endpoint is intended to drive the keyboard navigation of the app from the search input.
    """
    term = flask.request.args["q"].strip()

    options = []

    if term.startswith("http://") or term.startswith("https://"):
        # we can reasonably assume this is a url

        options += [
            ("Discover RSS", flask.url_for("feed_add", url=term), "fas fa-rss"),
            ("Add as entry", flask.url_for("entry_add", url=term), "fas fa-download", "POST"),
            ("View in reader", flask.url_for("entry_add", url=term, redirect=1), "fas fa-book-reader", "POST"),
        ]
        if current_user.kindle_email:
            options += [("Send to Kindle", flask.url_for("send_to_kindle", url=term), "fas fa-tablet-alt", "POST")]
    else:
        matching_feeds = db.session.execute(
            db.select(models.Feed.id, models.Feed.name)
            .filter(models.Feed.name.icontains(term), models.Feed.user_id == current_user.id)
            .distinct()
        ).all()
        options += [(name, flask.url_for("entry_list", feed_id=id), "far fa-list-alt") for (id, name) in matching_feeds]

        # search is less important than quick access but more than edit
        options.append(("Search: " + term, flask.url_for("entry_list", q=term), "fas fa-search"))

        options += [
            ("Edit " + name, flask.url_for("feed_edit", feed_id=id), "fas fa-edit") for (id, name) in matching_feeds
        ]

    # TODO home and favorites should have more priority than search
    static_options = [
        ("Home", flask.url_for("entry_list"), "fas fa-home"),
        ("Favorites", flask.url_for("favorites", favorited=True), "far fa-star"),
        ("Add Feed", flask.url_for("feed_add"), "fas fa-plus"),
        ("Manage Feeds", flask.url_for("feed_list"), "fas fa-edit"),
        ("Kindle setup", flask.url_for("kindle_add"), "fas fa-tablet-alt"),
        ("Kindle log", flask.url_for("sent_to_kindle"), "fas fa-tablet-alt"),
    ]
    for so in static_options:
        if term.lower() in so[0].lower():
            options.append(so)

    return flask.render_template("autocomplete_items.html", options=options)


@app.put("/pinned/<int:id>")
@login_required
def entry_pin(id):
    "Pin the given entry and return the updated list of pinned entries."
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)

    if not entry.pinned:
        entry.pinned = datetime.datetime.utcnow()
        db.session.commit()

    filters = dict(**flask.request.args)
    pinned = models.Entry.select_pinned(current_user.id, **filters)
    return flask.render_template("entry_list_page.html", is_pinned_list=True, filters=filters, entries=pinned)


@app.delete("/pinned/<int:id>")
@login_required
def entry_unpin(id):
    "Unpin the given entry and return the updated list of pinned entries."
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)

    entry.pinned = None
    db.session.commit()

    filters = dict(**flask.request.args)
    pinned = models.Entry.select_pinned(current_user.id, **filters)
    return flask.render_template("entry_list_page.html", is_pinned_list=True, filters=filters, entries=pinned)


@app.put("/favorites/<int:id>")
@login_required
def entry_favorite(id):
    "Favorite the given entry."
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)

    if not entry.favorited:
        entry.favorited = datetime.datetime.utcnow()
        db.session.commit()
    return "", 204


@app.delete("/favorites/<int:id>")
@login_required
def entry_unfavorite(id):
    "Unfavorite the given entry."
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)

    entry.favorited = None
    db.session.commit()
    return "", 204


@app.route("/feeds")
@login_required
def feed_list():
    feed_rows = db.session.execute(
        db.select(models.Feed, sa.func.count(1), sa.func.max(models.Entry.sort_date).label("updated"))
        .filter(models.Feed.user_id == current_user.id)
        .join(models.Entry, models.Feed.id == models.Entry.feed_id, isouter=True)
        .group_by(models.Feed)
        .order_by(sa.text("bucket desc"), sa.text("updated desc"))
    )

    return flask.render_template("feeds.html", feeds=feed_rows)


class FeedForm(Form):
    def _strip(value):
        return value.strip() if value else value

    # type is not included here because it's immutable after creation (disabled on edit, not submitted)
    name = StringField(filters=[_strip], validators=[InputRequired(message="name is required")])
    url = StringField(
        filters=[_strip], validators=[InputRequired(message="url is required"), URL(message="url must be a valid URL")]
    )
    folder = StringField(filters=[_strip], validators=[Optional()])
    filters = StringField(filters=[_strip], validators=[Optional()])


@app.get("/feeds/new")
@login_required
def feed_add():
    url = flask.request.args.get("url")
    name = None
    error_msg = None

    if url:
        result = feeds.discover(url)
        if result:
            (url, name) = result

        if not result or not url:
            error_msg = "RSS/Atom feed link not found at the given URL."

    folders = db.session.scalars(
        db.select(models.Feed.folder)
        .filter(models.Feed.folder.isnot(None), models.Feed.folder.isnot(""))
        .filter_by(user_id=current_user.id)
        .distinct()
    )

    form = FeedForm(data={"url": url, "name": name})
    return flask.render_template("feed_edit.html", form=form, folders=folders, error_msg=error_msg)


def _normalize_url(url):
    """Normalize a feed URL for duplicate detection: lowercase scheme/host, strip trailing slash and fragment."""
    parsed = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), parsed.params, parsed.query, "")
    )


@app.post("/feeds/new")
@login_required
def feed_add_submit():
    form = FeedForm(flask.request.form)
    folders = db.session.scalars(
        db.select(models.Feed.folder)
        .filter(models.Feed.folder.isnot(None), models.Feed.folder.isnot(""))
        .filter_by(user_id=current_user.id)
        .distinct()
    )

    if not form.validate():
        return flask.render_template("feed_edit.html", form=form, folders=folders)

    existing = db.session.scalar(db.select(models.Feed).filter_by(name=form.name.data, user_id=current_user.id))
    if existing:
        form.name.errors.append(f"A feed with name '{form.name.data}' already exists")
        return flask.render_template("feed_edit.html", form=form, folders=folders)

    normalized_url = _normalize_url(form.url.data)
    existing_urls = db.session.scalars(db.select(models.Feed.url).filter_by(user_id=current_user.id))
    if any(_normalize_url(u) == normalized_url for u in existing_urls):
        form.url.errors.append("this feed URL is already subscribed")
        return flask.render_template("feed_edit.html", form=form, folders=folders)

    from feedi import tasks

    values = {k: v for k, v in form.data.items() if v}
    values["type"] = flask.request.form.get("type", "rss")
    new_feed = feeds.add(current_user.id, values)

    # trigger a sync of this feed to fetch its entries.
    # making it blocking with .get() so we have entries to show on the redirect
    tasks.sync_feed(new_feed.id, new_feed.name).get()

    # NOTE it would be better to redirect to the feed itself, but since we load it async
    # we'd have to show a spinner or something and poll until it finishes loading
    # or alternatively hang the response until the feed is processed, neither of which is ideal
    return flask.redirect(flask.url_for("entry_list", feed_id=new_feed.id))


@app.get("/feeds/<feed_id>")
@login_required
def feed_edit(feed_id):
    feed = db.session.scalar(db.select(models.Feed).filter_by(id=feed_id, user_id=current_user.id))
    if not feed:
        flask.abort(404, "Feed not found")

    folders = db.session.scalars(
        db.select(models.Feed.folder)
        .filter(models.Feed.folder.isnot(None), models.Feed.folder.isnot(""))
        .filter_by(user_id=current_user.id)
        .distinct()
    ).all()

    form = FeedForm(obj=feed)
    return flask.render_template("feed_edit.html", feed=feed, form=form, folders=folders)


@app.post("/feeds/<feed_id>")
@login_required
def feed_edit_submit(feed_id):
    feed = db.session.scalar(db.select(models.Feed).filter_by(id=feed_id, user_id=current_user.id))
    if not feed:
        flask.abort(404, "Feed not found")

    form = FeedForm(flask.request.form)

    def _render_with_errors():
        folders = db.session.scalars(
            db.select(models.Feed.folder)
            .filter(models.Feed.folder.isnot(None), models.Feed.folder.isnot(""))
            .filter_by(user_id=current_user.id)
            .distinct()
        ).all()
        return flask.render_template("feed_edit.html", feed=feed, form=form, folders=folders)

    if not form.validate():
        return _render_with_errors()

    normalized_url = _normalize_url(form.url.data)
    existing_urls = db.session.scalars(
        db.select(models.Feed.url).filter(models.Feed.user_id == current_user.id, models.Feed.id != feed.id)
    )
    if any(_normalize_url(u) == normalized_url for u in existing_urls):
        form.url.errors.append("this feed URL is already subscribed")
        return _render_with_errors()

    # setting values at the instance level instead of issuing an update on models.Feed
    # so we don't need to explicitly inspect the feed to figure out its subclass
    for field in ("name", "url", "folder", "filters"):
        setattr(feed, field, form[field].data or None)
    db.session.commit()

    return flask.redirect(flask.url_for("feed_list"))


@app.delete("/feeds/<feed_id>")
@login_required
def feed_delete(feed_id):
    "Remove a feed and its entries from the database. Pinned and favorited entries are preserved."
    feed = db.session.scalar(db.select(models.Feed).filter_by(id=feed_id, user_id=current_user.id))

    if not feed:
        flask.abort(404, "Feed not found")

    feeds.delete(feed)
    return "", 204


@app.post("/feeds/<feed_id>/entries")
@login_required
def feed_sync(feed_id):
    "Force sync the given feed and redirect to the entry list for it."
    feed = db.session.scalar(db.select(models.Feed).filter_by(id=feed_id, user_id=current_user.id))
    if not feed:
        flask.abort(404, "Feed not found")

    from feedi import tasks

    task = tasks.sync_feed(feed.id, feed.name, force=True)
    task.get()

    response = flask.make_response()
    response.headers["HX-Redirect"] = flask.url_for("entry_list", feed_id=feed.id)
    return response


@app.post("/entries/")
@login_required
def entry_add():
    """
    Redirects to the content reader for the article at the given URL, creating a new entry for it
    if there isn't already one.
    """
    # TODO sanitize?
    url = flask.request.args["url"]
    redirect = flask.request.args.get("redirect")

    try:
        entry = entries.get_from_url(current_user.id, url)
    except Exception:
        if redirect:
            return _redirect_response(url)
        else:
            return "failed to parse entry", 500

    db.session.add(entry)
    db.session.commit()

    if redirect:
        return _redirect_response(flask.url_for("entry_view", id=entry.id))
    else:
        return "", 204


@app.get("/entries/<int:id>")
@login_required
def entry_view(id):
    """
    Display an entry for reading locally.

    Cached: render content directly.
    Uncached: render spinner → JS fetches /article → runs Readability → renders
              → POST /content to cache for future visits.
    """
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)
    if not entry.content_url and not entry.target_url:
        return "Entry not readable", 400
    if "youtube.com" in entry.content_url or "vimeo.com" in entry.content_url:
        return _redirect_response(entry.target_url)
    if entry.content_full:
        entry.viewed = entry.viewed or datetime.datetime.utcnow()
        db.session.commit()
    return flask.render_template("entry_content.html", entry=entry, content=entry.content_full)


@app.get("/entries/<int:id>/article")
@login_required
def entry_article(id):
    """
    Proxy the source HTML of the entry URL for client-side Readability processing.

    This is needed because readability runs in the client but the client can't make cross-origin requests.
    """
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)
    if not entry.content_url:
        flask.abort(400)
    response = requests.get(entry.content_url)
    response.raise_for_status()
    return flask.Response(response.content, content_type="text/html")


@app.post("/entries/<int:id>/content")
@login_required
def entry_save_content(id):
    """
    Cache the client-processed article content.

    The client builds the cleaned up version of the article (using readability), but
    that process should only run once, so this caches the result for subsequent visits.
    """
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)
    if not entry.content_full:
        entry.content_full = flask.request.json["content"]
        entry.viewed = entry.viewed or datetime.datetime.utcnow()
        db.session.commit()
    return "", 204


@app.post("/entries/kindle")
@login_required
def send_to_kindle():
    """
    Package an article as EPUB and email it to the user's Kindle device.

    JS sends the pre-extracted article as JSON. Two paths to get there:
    - from reader view: reuses already-extracted article
    - from list view: JS fetches /article → runs Readability → posts here
    """
    if not current_user.kindle_email:
        return "", 204

    data = flask.request.json
    url = data["url"]
    article = {k: data.get(k) for k in ["content", "title", "byline", "siteName", "publishedTime", "lang"]}
    entries.send_to_kindle(current_user, url, article)

    return "", 204


@app.route("/feeds/<feed_id>/debug")
@login_required
def raw_feed(feed_id):
    """
    Shows a JSON dump of the feed data as received from the source.
    """
    feed = db.session.scalar(
        db.select(models.Feed)
        .filter_by(id=feed_id, user_id=current_user.id)
        .options(sa.orm.undefer(models.Feed.raw_data))
    )
    if not feed:
        flask.abort(404, "Feed not found")

    return app.response_class(response=feed.raw_data, status=200, mimetype="application/json")


@app.route("/entries/<int:id>/debug")
@login_required
def raw_entry(id):
    """
    Shows a JSON dump of the entry data as received from the source.
    """
    entry = db.get_or_404(models.Entry, id, options=[sa.orm.undefer(models.Entry.raw_data)])

    if entry.user_id != current_user.id:
        flask.abort(404)

    return app.response_class(response=entry.raw_data, status=200, mimetype="application/json")


# TODO improve this views to accept only valid values
@app.put("/session/<setting>/<value>")
@login_required
def update_setting(setting, value):
    flask.session[setting] = value

    return "", 204


# TODO improve this views to accept only valid values
# also the default is dubious
@app.post("/session/<setting>")
@login_required
def toggle_setting(setting):
    flask.session[setting] = not flask.session.get(setting, True)
    return "", 204


def _redirect_response(url):
    """
    Issue the proper redirect depending on whether the current request
    is a regular one or an ajax/htmx one.
    """
    if "HX-Request" in flask.request.headers:
        response = flask.make_response()
        response.headers["HX-Redirect"] = url
        return response
    else:
        return flask.redirect(url)


@app.context_processor
def template_defaults():
    # templates expect this to exist
    return dict(filters={})


# Auth


login_manager = flask_login.LoginManager()
login_manager.login_view = "login"
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(models.User, int(user_id))


@app.get("/auth/login")
def login():
    # if config has a default user it means auth is disabled
    # just load the user so we know what to point feeds to in the DB
    default_email = app.config.get("DEFAULT_AUTH_USER")
    if default_email:
        app.logger.debug("Logging default user %s", default_email)
        user = db.session.scalar(db.select(models.User).filter_by(email=default_email))
        flask_login.login_user(user, remember=True)
        return flask.redirect(flask.url_for("entry_list"))

    return flask.render_template("login.html")


@app.post("/auth/login")
def login_post():
    email = flask.request.form.get("email")
    password = flask.request.form.get("password")
    if not email or not password:
        return flask.render_template("login.html", error_msg="missing required field")

    user = db.session.scalar(db.select(models.User).filter_by(email=email))

    if not user or not user.check_password(password):
        return flask.render_template("login.html", error_msg="authentication failed")

    flask_login.login_user(user, remember=True)

    return flask.redirect(flask.url_for("entry_list"))


@app.get("/auth/kindle")
@login_required
def kindle_add():
    feedi_email = app.config.get("FEEDI_EMAIL")
    if not feedi_email:
        return flask.abort(400, "no feedi email configured")
    return flask.render_template("kindle.html", feedi_email=feedi_email)


@app.post("/auth/kindle")
@login_required
def kindle_add_submit():
    kindle_email = flask.request.form.get("kindle_email")
    current_user.kindle_email = kindle_email
    db.session.commit()
    return flask.redirect(flask.url_for("entry_list"))


# Template filters


@app.template_filter("humanize")
def humanize_date(dt):
    delta = datetime.datetime.utcnow() - dt

    if delta < datetime.timedelta(seconds=60):
        return f"{delta.seconds}s"
    elif delta < datetime.timedelta(hours=1):
        return f"{delta.seconds // 60}m"
    elif delta < datetime.timedelta(days=1):
        return f"{delta.seconds // 60 // 60}h"
    elif delta < datetime.timedelta(days=8):
        return f"{delta.days}d"
    elif delta < datetime.timedelta(days=365):
        return dt.strftime("%b %d")
    return dt.strftime("%b %d, %Y")


@app.template_filter("url_domain")
def feed_domain(url):
    parts = urllib.parse.urlparse(url)
    return parts.netloc.replace("www.", "")


@app.template_filter("sanitize")
def sanitize_content(html):
    if not html:
        return ""

    soup = BeautifulSoup(html, "lxml")

    if soup.html:
        if soup.html.body:
            soup.html.body.unwrap()
        soup.html.unwrap()

    for a in soup.find_all("a", href=True):
        # prevent link clicks triggering the container's click event
        # add kb modifiers to open in reader
        read_url = flask.url_for("entry_add", url=a["href"], redirect=1)
        a["_"] = f"""
        on click[shiftKey and not metaKey] go to url {read_url} then halt
        then on click[shiftKey and metaKey] go to url {read_url} in new window then halt
        then on click halt the event's bubbling
        """

    return str(soup)


@app.template_filter("feed_name")
def feed_name(feed_id):
    feed = db.get_or_404(models.Feed, feed_id)
    return feed.name
