import flask
import flask_login
from flask import current_app as app
from werkzeug.security import check_password_hash

import feedi.models as models
from feedi.models import db


def init():
    login_manager = flask_login.LoginManager()
    login_manager.login_view = 'login'
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(models.User, int(user_id))


@app.route("/login/")
def login():
    # TODO add support for autologin config
    return flask.render_template('login.html')


@app.route('/login', methods=['POST'])
def login_post():
    email = flask.request.form.get('email')
    password = flask.request.form.get('password')
    if not email or not password:
        return flask.render_template('login.html', error_msg="missing required field")

    user = db.session.scalar(db.select(models.User).filter_by(email=email))

    if not user or not check_password_hash(user.password, password):
        return flask.render_template('login.html', error_msg="authentication failed")

    flask_login.login_user(user, remember=True)

    return flask.redirect(flask.url_for('entry_list'))
