# coding: utf-8

import datetime as dt
import re

from tests.conftest import create_feed, datafile, mock_feed


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


def test_sync_old_entries(client):
    # TODO
    # verify that RSS_SKIP_OLDER_THAN_DAYS is honored

    # verify that if the feed doesn't have enough entries
    # RSS_MINIMUM_ENTRY_AMOUNT is honored, regardless of entry age
    pass


def test_sync_updates(client):
    feed_domain = 'feed1.com'
    response = create_feed(client, feed_domain, [{'title': 'my-first-article', 'date': '2023-10-01 00:00Z',
                                                  'description': 'initial description'},
                                                 {'title': 'my-second-article', 'date': '2023-10-10 00:00Z'}])

    assert 'my-first-article' in response.text
    assert 'initial description' in response.text
    assert 'my-second-article' in response.text

    mock_feed(feed_domain, [{'title': 'my-first-article', 'date': '2023-10-01 00:00Z',
                             'description': 'updated description'},
                            {'title': 'my-second-article', 'date': '2023-10-10 00:00Z'},
                            {'title': 'my-third-article', 'date': '2023-10-11 00:00Z'}])

    # force resync
    response = client.post(f'/feeds/{feed_domain}/entries')
    assert response.status_code == 200

    # verify changes took effect
    response = client.get('/')
    assert 'my-first-article' in response.text
    assert 'updated description' in response.text
    assert 'initial description' not in response.text
    assert 'my-second-article' in response.text
    assert 'my-third-article' in response.text


def test_sync_between_pages(client):
    # TODO verify pagination behaves reasonably if new feeds/entries
    # are added between fetching one page and the next
    pass


def test_favorites(client):
    feed_domain = 'feed1.com'
    response = create_feed(client, feed_domain, [{'title': 'my-first-article', 'date': '2023-10-01 00:00Z'},
                                                 {'title': 'my-second-article', 'date': '2023-10-10 00:00Z'}])

    a2_favorite = re.search(r'/favorites/(\d+)', response.text).group(0)
    response = client.put(a2_favorite)
    assert response.status_code == 204

    response = client.get('/favorites')
    assert 'my-first-article' not in response.text
    assert 'my-second-article' in response.text


def test_pinned(client):
    response = create_feed(client, 'feed1.com', [{'title': 'f1-a1', 'date': '2023-10-01 00:00Z'},
                                                 {'title': 'f1-a2', 'date': '2023-10-10 00:00Z'}],
                           folder='folder1')
    f1a2_pin_url = re.search(r'/pinned/(\d+)', response.text).group(0)

    response = create_feed(client, 'feed2.com', [{'title': 'f2-a1', 'date': '2023-10-01 00:00Z'},
                                                 {'title': 'f2-a2', 'date': '2023-10-10 00:00Z'}])
    f2_a2_pin_url = re.search(r'/pinned/(\d+)', response.text).group(0)

    response = client.get('/')
    assert 'f1-a2' in response.text
    assert 'f2-a2' in response.text
    response = client.get('/folder/folder1')
    assert 'f1-a2' in response.text
    assert 'f2-a2' not in response.text

    # add some pages of more entries in both feeds, to ensure the older ones are pushed out of the page
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(1, 20):
        date = now - dt.timedelta(hours=1, minutes=1)
        create_feed(client, f'f{i}-folder1.com', [{'title': 'article1', 'date': date}],
                    folder='folder1')

    # verify the old entries where pushed out of home and folder
    response = client.get('/')
    assert 'f1-a2' not in response.text
    assert 'f2-a2' not in response.text
    response = client.get('/folder/folder1')
    assert 'f1-a2' not in response.text

    # pin the old entries
    response = client.put(f1a2_pin_url)
    assert response.status_code == 200
    response = client.put(f2_a2_pin_url)
    assert response.status_code == 200

    # verify they are pinned to the home and folder
    response = client.get('/')
    assert 'f1-a2' in response.text
    assert 'f2-a2' in response.text
    response = client.get('/folder/folder1')
    assert 'f1-a2' in response.text
    assert 'f2-a2' not in response.text


def test_entries_not_mixed_between_users(client):
    # TODO
    pass


def test_view_entry_content(client):
    # create feed with a sample entry
    body = datafile('sample.html')
    response = create_feed(client, 'olano.dev', [{'title': 'reclaiming-the-web',
                                                  'date': '2023-12-12T00:00:00-03:00',
                                                  'description': 'short content',
                                                  'body': body}])
    assert 'reclaiming-the-web' in response.text
    assert 'short content' in response.text
    entry_url = re.search(r'/entries/(\d+)', response.text).group(0)
    response = client.get(entry_url)

    assert 'reclaiming-the-web' in response.text
    assert 'I had some ideas of what I wanted' in response.text


def test_add_external_entry(client):
    # mock response to an arbitrary url
    # add a standalone entry for that url
    # extract redirected entry url
    # verify content parsed
    # add same url again
    # verify that redirected entry url is the same as before
    # TODO
    pass


def test_discover_feed(client):
    # TODO
    pass


def test_feed_list(client):
    # TODO
    pass


def test_feed_edit(client):
    # TODO
    pass


def test_feed_delete(client):
    # TODO
    pass


def test_mastodon_feed(client):
    # TODO mock mastodon api requests
    # check that entries show up in feed
    pass
