import os

import pytest
from feedi.app import create_app
from feedi.models import db


@pytest.fixture()
def app():
    assert os.getenv('FLASK_ENV') == 'testing', "not running in testing mode"

    app = create_app()
    yield app

    # clean up / reset resources
    with app.app_context():
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def test_feed_add(client):
    # setup a feed rss url with a couple of items

    # create a new feed with a form post

    # assert it redirects to feed's feed

    # assert it displays the items sorted by publish date
    assert 1 == 1, "1 equals 1"


def test_home():
    pass


def test_home_freq_sort():
    pass


def test_home_recency_sort():
    pass


def test_home_pagination():
    pass


def test_auto_mark_viewed():
    pass


def test_folder():
    pass


def test_sync_while_between_pages():
    # TODO
    pass
