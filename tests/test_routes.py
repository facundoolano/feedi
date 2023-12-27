import os

import feedgen.feed as feedgen
import feedi.app as feedi_app
import pytest
import responses  # this should come after app import
from feedi.models import db


@responses.activate
@pytest.fixture()
def app():
    assert os.getenv('FLASK_ENV') == 'testing', "not running in testing mode"

    app = feedi_app.create_app()
    yield app

    # clean up / reset resources
    with app.app_context():
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def test_feed_add(client):
    # setup a feed rss url with a couple of items
    mock_feed('feed1.com', [{'title': 'my-first-article', 'date': '2023-10-01'},
                            {'title': 'my-second-article', 'date': '2023-10-10'}
                            ])

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


def mock_feed(domain, items):
    base_url = f'https://{domain}'
    feed_url = f'{base_url}/feed'

    fg = feedgen.FeedGenerator()
    fg.id(base_url)
    fg.link(href=feed_url)
    fg.title(f'{domain} feed')
    fg.description(f'{domain} feed')

    for item in items:
        entry = fg.add_entry()
        entry.id()
        entry.link(href=f'{base_url}/{item["title"]}')
        entry.title(item['title'])
        entry.author({"name": 'John Doe'})
        entry.published(item['date'] + ' 00:00Z')

    rssfeed = fg.rss_str()
    responses.add(responses.get(feed_url, body=rssfeed,
                  headers={'Content-Type': 'application/xml'}))
