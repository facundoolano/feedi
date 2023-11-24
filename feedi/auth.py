import urllib

import flask
import flask_login
from flask import current_app as app
from flask_login import current_user, login_required

import feedi.models as models
from feedi.models import db
from feedi.parsers import mastodon


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
    return flask.render_template('mastodon.html')


@app.post("/auth/mastodon")
@login_required
def mastodon_oauth_submit():
    """TODO"""
    # sanitize base url
    base_url = flask.request.form.get('url')
    if not base_url:
        return flask.render_template('mastodon.html', error_msg="The instance url is required")

    url_parts = urllib.parse.urlparse(base_url)
    base_url = f'https://{url_parts.netloc}'

    # if not already registered, register with mastopy and save to db
    masto_app = db.session.scalar(db.select(models.MastodonApp).filter_by(api_base_url=base_url))
    if not masto_app:
        client_id, client_secret = mastodon.register_app(base_url)
        masto_app = models.MastodonApp(api_base_url=base_url,
                                       client_id=client_id,
                                       client_secret=client_secret)
        db.session.add(masto_app)
        db.session.commit()

    redirect_url = mastodon.auth_redirect_url(masto_app.api_base_url,
                                              masto_app.client_id,
                                              masto_app.client_secret,
                                              flask.url_for('mastodon_oauth_calback',
                                                            appid=masto_app.id,
                                                            _external=True))
    return flask.redirect(redirect_url)


@app.get("/auth/mastodon/callback")
@login_required
def mastodon_oauth_calback():
    """TODO"""

    appid = flask.request.form.get('appid')
    code = flask.request.form.get('code')
    if not appid or not code:
        flask.abort(400)

    masto_app = db.get_or_404(models.MastodonApp, int(appid))

    # mastodon api requires passing again the already used callback url
    callback_url = flask.url_for('mastodon_oauth_calback',
                                 appid=masto_app.id,
                                 _external=True)
    access_token = mastodon.oauth_login(masto_app.api_base_url, code, callback_url)

    # store the token in the masto accounts table
    masto_acct = models.MastodonAccount(app_id=masto_app.id,
                                        user_id=current_user.id,
                                        access_token=access_token)
    db.session.add(masto_acct)
    db.session.commit()

    # redirect to feed creation with masto pre-selected
    return flask.redirect(flask.url_for('feed_add', masto_acct=masto_acct.id))
