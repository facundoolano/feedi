import datetime
import os
import uuid

import feedgen.feed as feedgen
import feedi.app as feedi_app
import httpretty
import pytest
from feedi.models import db


@pytest.fixture(scope='module')
def app():
    assert os.getenv('FLASK_ENV') == 'testing', "not running in testing mode"

    app = feedi_app.create_app()

    httpretty.enable(allow_net_connect=False, verbose=True)

    yield app

    httpretty.disable()

    # clean up / reset resources
    with app.app_context():
        db.drop_all()


@pytest.fixture
def client(app):
    "Return a test client authenticated with a fresh user."

    email = f'user-{uuid.uuid4()}@mail.com'
    with app.app_context():
        # kind of lousy to interact with DB directly, but need to work around
        # user registering not exposed to the web
        from feedi import models
        user = models.User(email=email)
        user.set_password('password')
        db.session.add(user)
        db.session.commit()

    client = app.test_client()
    response = client.post(
        '/auth/login', data={'email': email, 'password': 'password'}, follow_redirects=True)
    assert response.status_code == 200

    httpretty.reset()
    return client


def test_feed_add(client):
    feed_domain = 'feed1.com'
    feed_url = mock_feed(feed_domain, [{'title': 'my-first-article', 'date': '2023-10-01 00:00Z'},
                                       {'title': 'my-second-article', 'date': '2023-10-10 00:00Z'}])

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

    # check same entries show up in home feed
    response = client.get('/')
    assert response.status_code == 200

    assert 'my-first-article' in response.text, 'article should be included in entry list'
    assert 'my-second-article' in response.text, 'article should be included in entry list'
    assert response.text.find(
        'my-second-article') < response.text.find('my-first-article'), 'articles should be sorted by publication date'


def test_folders():
    # feed1, feed2 -> folder 1
    # feed3 -> folder 2
    # feed4 -> no folder

    # get home
    # get folder1
    # get folder2
    pass


def test_home_freq_sort():
    pass


def test_home_recency_sort():
    pass


def test_home_pagination():
    pass


def test_auto_mark_viewed():
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
        entry.published(item['date'])
        entry.updated(item['date'])

        mock_request(entry_url, body='<p>content!</p>')

    rssfeed = fg.rss_str()
    mock_request(base_url)
    mock_request(f'{base_url}/favicon.ico', ctype='image/x-icon')
    mock_request(feed_url, body=rssfeed, ctype='application/rss+xml')

    return feed_url


def mock_request(url, body='', ctype='application/html'):
    httpretty.register_uri(httpretty.HEAD, url, adding_headers={
                           'Content-Type': ctype}, priority=1)
    httpretty.register_uri(httpretty.GET, url, body=body, adding_headers={
                           'Content-Type': ctype}, priority=1)
