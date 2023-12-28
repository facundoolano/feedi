import datetime
import os
import uuid

import feedgen.feed as feedgen
import feedi.app as feedi_app
import httpretty
import pytest
from feedi.models import db

######## SETUP ##########


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


######## TESTS ##########

def test_feed_add(client):
    feed_domain = 'feed1.com'
    response = create_feed(client, feed_domain, [{'title': 'my-first-article', 'date': '2023-10-01 00:00Z'},
                                                 {'title': 'my-second-article', 'date': '2023-10-10 00:00Z'}])

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


def test_folders(client):
    # feed1, feed2 -> folder 1
    create_feed(client, 'feed1.com', [{'title': 'f1-a1', 'date': '2023-10-01 00:00Z'},
                                      {'title': 'f1-a2', 'date': '2023-10-10 00:00Z'}],
                folder='folder1')

    create_feed(client, 'feed2.com', [{'title': 'f2-a1', 'date': '2023-10-01 00:00Z'},
                                      {'title': 'f2-a2', 'date': '2023-10-10 00:00Z'}],
                folder='folder1')

    # feed3 -> folder 2
    create_feed(client, 'feed3.com', [{'title': 'f3-a1', 'date': '2023-10-01 00:00Z'},
                                      {'title': 'f3-a2', 'date': '2023-10-10 00:00Z'}],
                folder='folder2')

    # feed4 -> no folder
    create_feed(client, 'feed4.com', [{'title': 'f4-a1', 'date': '2023-10-01 00:00Z'},
                                      {'title': 'f4-a2', 'date': '2023-10-10 00:00Z'}])

    response = client.get('/')
    assert all([feed in response.text for feed in ['f1-a1', 'f1-a2',
               'f2-a1', 'f2-a2', 'f3-a1', 'f3-a2', 'f4-a1', 'f4-a2']])

    response = client.get('/folder/folder1')
    assert all([feed in response.text for feed in ['f1-a1', 'f1-a2', 'f2-a1', 'f2-a2']])
    assert all([feed not in response.text for feed in ['f3-a1', 'f3-a2', 'f4-a1', 'f4-a2']])

    response = client.get('/folder/folder2')
    assert all([feed in response.text for feed in ['f3-a1', 'f3-a2']])
    assert all([feed not in response.text for feed in [
               'f1-a1', 'f1-a2', 'f2-a1', 'f2-a2', 'f4-a1', 'f4-a2']])


def test_home_freq_sort():
    pass


def test_home_recency_sort():
    pass


def test_home_pagination():
    pass


def test_auto_mark_viewed():
    pass


##### HELPERS #######

def create_feed(client, domain, items, folder=None):
    feed_url = mock_feed(domain, items)

    # create a new feed with a form post
    return client.post('/feeds/new', data={
        'type': 'rss',
        'name': domain,
        'url': feed_url,
        'folder': folder
    }, follow_redirects=True)


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
