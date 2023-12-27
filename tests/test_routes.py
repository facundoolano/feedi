import pytest
from feedi.app import create_app
from feedi.models import db


@pytest.fixture()
def app():
    app = create_app()
    app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///feedi.test.db"
    })

    # other setup can go here

    yield app

    # clean up / reset resources here
    with app.app_context():
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def test_feed_add(client):
    assert 1 == 1, "1 equals 1"
