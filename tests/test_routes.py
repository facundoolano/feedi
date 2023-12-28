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


def test_home_freq_sort():
    pass


def test_home_recency_sort():
    pass


def test_home_pagination():
    pass


def test_auto_mark_viewed():
    pass
