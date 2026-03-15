import click
import flask

import feedi.models as models
import feedi.parsers.rss as rss
from feedi.models import db
from feedi.services import feeds

feed_cli = flask.cli.AppGroup("feed")
user_cli = flask.cli.AppGroup("user")


def register(app):
    app.cli.add_command(feed_cli)
    app.cli.add_command(user_cli)


def load_user_arg(_ctx, _param, email):
    """
    CLI argument callback to load a user. If a user email is not provided explicitly,
    fallback to the DEFAULT_AUTH_USER from the settings, otherwise raise an error.
    """
    from flask import current_app as app

    if not email:
        email = app.config.get("DEFAULT_AUTH_USER")
        if not email:
            raise click.UsageError("No user provided and no DEFAULT_AUTH_USER set")

    user = db.session.scalar(db.select(models.User).filter_by(email=email))
    if not user:
        raise click.UsageError(f"User {email} not found")
    return user


@feed_cli.command("sync")
def feed_sync():
    from feedi import tasks

    tasks.sync_all_feeds().get()


@feed_cli.command("prefetch")
def feed_prefetch():
    from feedi import tasks

    tasks.content_prefetch().get()


@feed_cli.command("purge")
def feed_purge():
    from feedi import tasks

    tasks.delete_old_entries().get()


@feed_cli.command("debug")
@click.argument("url")
def debug_feed(url):
    rss.pretty_print(url)


@feed_cli.command("load")
@click.argument("file")
@click.argument("user", required=False, callback=load_user_arg)
def feed_load(file, user):
    "Load feeds from a local csv file."
    feeds.import_csv(user, file)


@feed_cli.command("dump")
@click.argument("file")
@click.argument("user", required=False, callback=load_user_arg)
def feed_dump(file, user):
    "Dump feeds to a local csv file."
    feeds.export_csv(user, file)


@feed_cli.command("load-opml")
@click.argument("file")
@click.argument("user", required=False, callback=load_user_arg)
def feed_load_opml(file, user):
    feeds.import_opml(user, file)


@feed_cli.command("dump-opml")
@click.argument("file")
@click.argument("user", required=False, callback=load_user_arg)
def feed_dump_opml(file, user):
    feeds.export_opml(user, file)


@feed_cli.command("recalculate-buckets")
def feed_recalculate_buckets():
    """Recalculate frequency buckets for all feeds."""
    feeds.recalculate_buckets()


@user_cli.command("add")
@click.argument("email")
@click.password_option()
def user_add(email, password):
    user = models.User(email=email)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()


@user_cli.command("del")
@click.argument("email")
def user_delete(email):
    stmt = db.delete(models.User).where(models.User.email == email)
    db.session.execute(stmt)
    db.session.commit()
