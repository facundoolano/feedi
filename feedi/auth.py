import urllib

import flask
import flask_login
from flask import current_app as app
from flask_login import current_user, login_required

import feedi.models as models
from feedi.models import db


def init():
    login_manager = flask_login.LoginManager()
    login_manager.login_view = 'login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))


@app.get("/auth/login")
def login():
    # if config has a default user it means auth is disabled
    # just load the user so we know what to point feeds to in the DB
    default_email = app.config.get('DEFAULT_AUTH_USER')
    if default_email:
        app.logger.debug("Logging default user %s", default_email)
        user = db.session.scalar(db.select(models.User).filter_by(email=default_email))
        flask_login.login_user(user, remember=True)
        return flask.redirect(flask.url_for('entry_list'))

    return flask.render_template('login.html')


@app.post('/auth/login')
def login_post():
    email = flask.request.form.get('email')
    password = flask.request.form.get('password')
    if not email or not password:
        return flask.render_template('login.html', error_msg="missing required field")

    user = db.session.scalar(db.select(models.User).filter_by(email=email))

    if not user or not user.check_password(password):
        return flask.render_template('login.html', error_msg="authentication failed")

    flask_login.login_user(user, remember=True)

    return flask.redirect(flask.url_for('entry_list'))


@app.get("/auth/kindle")
@login_required
def kindle_add():
    verifier, url = models.KindleDevice.signin_url()
    return flask.render_template('kindle.html', signin_url=url, verifier=verifier)


@app.post("/auth/kindle")
@login_required
def kindle_add_submit():
    verifier = flask.request.form.get('verifier')
    redirect_url = flask.request.form.get('redirect_url')
    models.KindleDevice.add_from_url(current_user.id, verifier, redirect_url)
    db.session.commit()
    return flask.redirect(flask.url_for('entry_list'))


@app.get("/auth/mastodon")
@login_required
def mastodon_oauth():
    "Displays the form to initiate a mastodon oauth login flow."
    return flask.render_template('mastodon.html')


@app.post("/auth/mastodon")
@login_required
def mastodon_oauth_submit():
    """
    Starts the Oauth login flow to a user submitted mastodon instance.
    If there's no app already registered for that instance, one is created.
    Returns a redirect to the mastodon authorization url on that instance, which
    will then redirect to the callback route.
    """
    base_url = flask.request.form.get('url')
    if not base_url:
        return flask.render_template('mastodon.html', error_msg="The instance url is required")

    # normalize base url
    url_parts = urllib.parse.urlparse(base_url)
    base_url = f'https://{url_parts.netloc}'

    app = models.MastodonApp.get_or_create(base_url)
    return flask.redirect(app.auth_redirect_url())


@app.get("/auth/mastodon/callback")
@login_required
def mastodon_oauth_callback():
    """
    The route the user will be redirected to after granting feedi permission to access
    the mastodon account. The account will be logged in with the received authorization code
    and an access token will be stored in the DB for subsequent access to the mastodon api.
    Redirects to the feed add form to proceed creating a mastodon feed associated with the new account.
    """
    code = flask.request.args.get('code')
    base_url = flask.request.args.get('server')
    if not code or not base_url:
        app.logger.error("Missing required parameter in mastodon oauth callback")
        flask.abort(400)

    masto_app = db.session.scalar(db.select(models.MastodonApp).filter_by(api_base_url=base_url))
    if not masto_app:
        app.logger.error("Mastodon application not found for %s", base_url)
        flask.abort(404)

    app.logger.info("Authenticating mastodon user %s at %s", current_user.id, base_url)
    account = masto_app.create_account(current_user.id, code)
    app.logger.info("Successfully logged in mastodon")

    # redirect to feed creation with masto pre-selected
    return flask.redirect(flask.url_for('feed_add', masto_acct=account.id))
