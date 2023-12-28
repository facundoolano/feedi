import datetime as dt
import re

from tests.setup import app, client, create_feed


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


def test_home_sorting(client):
    # feed1: 1 post 12 hs ago
    date12h = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    create_feed(client, 'feed1.com', [{'title': 'f1-a1', 'date': date12h}])

    # feed2: 20 posts < 12 hs ago
    items = []
    for i in range(1, 21):
        items.append({'title': f'f2-a{i}', 'date': date12h + dt.timedelta(hours=1, minutes=i)})
    create_feed(client, 'feed2.com', items)

    # home shows f1 post first
    # the rest are in chronological order
    response = client.get('/')
    assert response.text.find('f1-a1') < response.text.find('f2-a20')
    assert response.text.find('f2-a20') < response.text.find('f2-a12')

    # feed3: 1 post 13 hs ago
    date13h = date12h - dt.timedelta(hours=1)
    create_feed(client, 'feed3.com', [{'title': 'f3-a1', 'date': date13h}])

    response = client.get('/')
    assert response.text.find('f1-a1') < response.text.find('f3-a1')
    assert response.text.find('f3-a1') < response.text.find('f2-a13')

    # change the sorting settings and request home again
    client.put('/session/ordering/recency')
    response = client.get('/')
    assert response.text.find('f2-a20') < response.text.find('f2-a12')
    assert 'f3-a1' not in response.text
    assert 'f1-a1' not in response.text


def test_home_pagination(app, client):
    now = dt.datetime.now(dt.timezone.utc)
    items = []
    per_page = app.config['ENTRY_PAGE_SIZE']
    for i in range(0, per_page * 3):
        items.append({'title': f'f1-a{i}', 'date': now - dt.timedelta(hours=3, minutes=i)})
    create_feed(client, 'feed1.com', items)

    # home includes a first page of results, sorted by pub date
    response = client.get('/')
    assert 'f1-a0' in response.text
    assert f'f1-a{per_page - 1}' in response.text
    assert f'f1-a{per_page}' not in response.text
    assert response.text.find('f1-a0') < response.text.find(f'f1-a{per_page - 1}')

    next_page = re.search(r'page=([^&"]+)', response.text).group(1)
    response = client.get(f'/?page={next_page}')
    assert f'f1-a{per_page - 1}' not in response.text
    assert f'f1-a{per_page}' in response.text
    assert f'f1-a{per_page * 2 - 1}' in response.text
    assert f'f1-a{per_page * 2}' not in response.text

    # get home again without page, verify the first page was marked as already seen
    response = client.get('/')
    assert f'f1-a{per_page - 1}' not in response.text
    assert f'f1-a{per_page}' in response.text
    assert f'f1-a{per_page * 2 - 1}' in response.text
    assert f'f1-a{per_page * 2}' not in response.text

    # change settings to include already seen
    response = client.post('/session/hide_seen')
    assert response.status_code == 204

    # get home again, verify first page is included again
    response = client.get('/')
    assert 'f1-a0' in response.text
    assert f'f1-a{per_page - 1}' in response.text
    assert f'f1-a{per_page}' not in response.text
