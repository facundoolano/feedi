import os

import feedgen.feed as feedgen
import feedi.app as feedi_app
import httpretty
import pytest
from feedi.models import db


@pytest.fixture()
def app():
    assert os.getenv('FLASK_ENV') == 'testing', "not running in testing mode"

    httpretty.enable(allow_net_connect=False, verbose=True)

    app = feedi_app.create_app()

    yield app

    httpretty.disable()

    # clean up / reset resources
    with app.app_context():
        db.session.flush()
        # FIXME
        # db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def test_feed_add(client):
    # get the index to force a default login
    response = client.get('/', follow_redirects=True)
    assert response.status_code == 200

    feed_domain = 'feed1.com'
    feed_url = mock_feed(feed_domain, [{'title': 'my-first-article', 'date': '2023-10-01'},
                                       {'title': 'my-second-article', 'date': '2023-10-10'}])

    # create a new feed with a form post
    response = client.post('/feeds/new', data={
        'type': 'rss',
        'name': feed_domain,
        'url': feed_url
    }, follow_redirects=True)

    assert response.status_code == 200
    assert response.request.path == f'/feeds/{feed_domain}/entries', 'feed submit should redirect to entry list'

    assert 'my-first-article' in response.text, 'article should be included in entry list'
    assert 'my-second-article' in response.text, 'article should be included in entry list'
    assert response.text.find(
        'my-second-article') < response.text.find('my-first-article'), 'articles should be sorted by publication date'


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
        entry_url = f'{base_url}/{item["title"]}'
        entry = fg.add_entry()
        entry.id()
        entry.link(href=entry_url)
        entry.title(item['title'])
        entry.author({"name": 'John Doe'})
        entry.published(item['date'] + ' 00:00Z')
        entry.updated(item['date'] + ' 00:00Z')

        mock_request(entry_url, body='<p>content!</p>')

    rssfeed = fg.rss_str()
    mock_request(feed_url, body=rssfeed, ctype='application/rss+xml')
    mock_request(
        base_url, body='<html><head><link rel="icon" type="image/x-icon" href="/favicon.ico"></head></html>')
    mock_request(f'{base_url}/favicon.ico', ctype='image/x-icon')

    return feed_url


def mock_request(url, body='', ctype='application/html'):
    httpretty.register_uri(httpretty.GET, url, body=body, adding_headers={
                           'Content-Type': ctype})
