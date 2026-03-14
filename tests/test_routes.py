import datetime as dt
import re

from tests.conftest import create_feed, create_user, extract_entry_ids, mock_feed, mock_request


def test_feed_add(client):
    feed_domain = "feed1.com"
    response, feed_id = create_feed(
        client,
        feed_domain,
        [
            {"title": "my-first-article", "date": "2023-10-01 00:00Z"},
            {"title": "my-second-article", "date": "2023-10-10 00:00Z"},
        ],
    )

    assert response.status_code == 200
    assert response.request.path == f"/feeds/{feed_id}/entries", "feed submit should redirect to entry list"

    assert "my-first-article" in response.text, "article should be included in entry list"
    assert "my-second-article" in response.text, "article should be included in entry list"
    assert response.text.find("my-second-article") < response.text.find("my-first-article"), (
        "articles should be sorted by publication date"
    )

    # check same entries show up in home feed
    response = client.get("/")
    assert response.status_code == 200

    assert "my-first-article" in response.text, "article should be included in entry list"
    assert "my-second-article" in response.text, "article should be included in entry list"
    assert response.text.find("my-second-article") < response.text.find("my-first-article"), (
        "articles should be sorted by publication date"
    )


def test_folders(client):
    # feed1, feed2 -> folder 1
    create_feed(
        client,
        "feed1.com",
        [{"title": "f1-a1", "date": "2023-10-01 00:00Z"}, {"title": "f1-a2", "date": "2023-10-10 00:00Z"}],
        folder="folder1",
    )

    create_feed(
        client,
        "feed2.com",
        [{"title": "f2-a1", "date": "2023-10-01 00:00Z"}, {"title": "f2-a2", "date": "2023-10-10 00:00Z"}],
        folder="folder1",
    )

    # feed3 -> folder 2
    create_feed(
        client,
        "feed3.com",
        [{"title": "f3-a1", "date": "2023-10-01 00:00Z"}, {"title": "f3-a2", "date": "2023-10-10 00:00Z"}],
        folder="folder2",
    )

    # feed4 -> no folder
    create_feed(
        client,
        "feed4.com",
        [{"title": "f4-a1", "date": "2023-10-01 00:00Z"}, {"title": "f4-a2", "date": "2023-10-10 00:00Z"}],
    )

    response = client.get("/")
    assert all(
        [feed in response.text for feed in ["f1-a1", "f1-a2", "f2-a1", "f2-a2", "f3-a1", "f3-a2", "f4-a1", "f4-a2"]]
    )

    response = client.get("/folder/folder1")
    assert all([feed in response.text for feed in ["f1-a1", "f1-a2", "f2-a1", "f2-a2"]])
    assert all([feed not in response.text for feed in ["f3-a1", "f3-a2", "f4-a1", "f4-a2"]])

    response = client.get("/folder/folder2")
    assert all([feed in response.text for feed in ["f3-a1", "f3-a2"]])
    assert all([feed not in response.text for feed in ["f1-a1", "f1-a2", "f2-a1", "f2-a2", "f4-a1", "f4-a2"]])


def test_home_sorting(client):
    # feed1: 1 post 12 hs ago
    date12h = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=12)
    create_feed(client, "feed1.com", [{"title": "f1-a1", "date": date12h}])

    # feed2: 20 posts < 12 hs ago
    items = []
    for i in range(1, 21):
        items.append({"title": f"f2-a{i}", "date": date12h + dt.timedelta(hours=1, minutes=i)})
    create_feed(client, "feed2.com", items)

    # home shows f1 post first
    # the rest are in chronological order
    response = client.get("/")
    assert response.text.find("f1-a1") < response.text.find("f2-a20")
    assert response.text.find("f2-a20") < response.text.find("f2-a12")

    # feed3: 1 post 13 hs ago
    date13h = date12h - dt.timedelta(hours=1)
    create_feed(client, "feed3.com", [{"title": "f3-a1", "date": date13h}])

    response = client.get("/")
    assert response.text.find("f1-a1") < response.text.find("f3-a1")
    assert response.text.find("f3-a1") < response.text.find("f2-a13")


def test_home_sorting_by_bucket(client):
    """Within the recency window, entries from less frequent feeds appear before entries
    from more frequent feeds, even when the frequent feed has more recent entries."""
    now = dt.datetime.now(dt.timezone.utc)

    # feed1: 1 post (bucket 0), posted 2h ago
    create_feed(client, "feed1.com", [{"title": "infrequent-post", "date": now - dt.timedelta(hours=2)}])

    # feed2: 20 posts (bucket 4+), all posted 1h ago — more recent than feed1
    items = [{"title": f"frequent-post-{i}", "date": now - dt.timedelta(hours=1, minutes=i)} for i in range(20)]
    create_feed(client, "feed2.com", items)

    response = client.get("/")
    assert response.text.find("infrequent-post") < response.text.find("frequent-post-0")


def test_home_sorting_recency_boundary(app, client):
    """Entries older than the recency window (72h) appear after recent entries,
    even when the older entries come from a lower-bucket (less frequent) feed."""
    now = dt.datetime.now(dt.timezone.utc)
    per_page = app.config["ENTRY_PAGE_SIZE"]

    # feed1: very infrequent (bucket 0), but its only entry is 4 days old
    create_feed(client, "feed1.com", [{"title": "old-infrequent", "date": now - dt.timedelta(hours=97)}])

    # feed2: several recent entries within the 72h window; fewer than per_page so both feeds fit on one page
    recent_count = per_page // 2
    recent_items = [{"title": f"new-frequent-{i}", "date": now - dt.timedelta(hours=1, minutes=i)} for i in range(recent_count)]
    create_feed(client, "feed2.com", recent_items)

    response = client.get("/")
    assert response.text.find("new-frequent-0") < response.text.find("old-infrequent")


def test_home_pagination(app, client):
    now = dt.datetime.now(dt.timezone.utc)
    items = []
    per_page = app.config["ENTRY_PAGE_SIZE"]
    for i in range(0, per_page * 3):
        items.append({"title": f"f1-a{i}", "date": now - dt.timedelta(hours=3, minutes=i)})
    create_feed(client, "feed1.com", items)

    # home includes a first page of results, sorted by pub date
    response = client.get("/")
    assert "f1-a0" in response.text
    assert f"f1-a{per_page - 1}" in response.text
    assert f"f1-a{per_page}" not in response.text
    assert response.text.find("f1-a0") < response.text.find(f"f1-a{per_page - 1}")

    next_page = re.search(r'page=([^&"]+)', response.text).group(1)
    response = client.get(f"/?page={next_page}")
    assert f"f1-a{per_page - 1}" not in response.text
    assert f"f1-a{per_page}" in response.text
    assert f"f1-a{per_page * 2 - 1}" in response.text
    assert f"f1-a{per_page * 2}" not in response.text

    # get home again without page, verify the first page was marked as already seen
    response = client.get("/")
    assert f"f1-a{per_page - 1}" not in response.text
    assert f"f1-a{per_page}" in response.text
    assert f"f1-a{per_page * 2 - 1}" in response.text
    assert f"f1-a{per_page * 2}" not in response.text

    # change settings to include already seen
    response = client.post("/session/hide_seen")
    assert response.status_code == 204

    # get home again, verify first page is included again
    response = client.get("/")
    assert "f1-a0" in response.text
    assert f"f1-a{per_page - 1}" in response.text
    assert f"f1-a{per_page}" not in response.text


def test_sync_old_entries(app, client):
    """Old entries past the skip threshold are excluded, unless the feed has fewer
    than the configured minimum, in which case old entries fill the gap."""
    now = dt.datetime.now(dt.timezone.utc)
    old_date = now - dt.timedelta(days=app.config["RSS_SKIP_OLDER_THAN_DAYS"] + 5)
    min_amount = app.config["RSS_MINIMUM_ENTRY_AMOUNT"]

    # Feed with more old entries than the minimum: only min_amount loaded
    items = [{"title": f"old-a{i}", "date": old_date - dt.timedelta(days=i)} for i in range(min_amount + 5)]
    response, _ = create_feed(client, "feed1.com", items)
    loaded = set(re.findall(r"old-a\d+", response.text))
    assert len(loaded) == min_amount

    # Feed with fewer old entries than the minimum: all loaded
    items = [{"title": f"few-a{i}", "date": old_date - dt.timedelta(days=i)} for i in range(min_amount - 3)]
    response, _ = create_feed(client, "feed2.com", items)
    assert all(f"few-a{i}" in response.text for i in range(min_amount - 3))


def test_sync_updates(client):
    feed_domain = "feed1.com"
    response, feed_id = create_feed(
        client,
        feed_domain,
        [
            {"title": "my-first-article", "date": "2023-10-01 00:00Z", "description": "initial description"},
            {"title": "my-second-article", "date": "2023-10-10 00:00Z"},
        ],
    )

    assert "my-first-article" in response.text
    assert "initial description" in response.text
    assert "my-second-article" in response.text

    mock_feed(
        feed_domain,
        [
            {"title": "my-first-article", "date": "2023-10-01 00:00Z", "description": "updated description"},
            {"title": "my-second-article", "date": "2023-10-10 00:00Z"},
            {"title": "my-third-article", "date": "2023-10-11 00:00Z"},
        ],
    )

    # force resync
    response = client.post(f"/feeds/{feed_id}/entries")
    assert response.status_code == 200

    # verify changes took effect
    response = client.get("/")
    assert "my-first-article" in response.text
    assert "updated description" in response.text
    assert "initial description" not in response.text
    assert "my-second-article" in response.text
    assert "my-third-article" in response.text


def test_sync_between_pages(app, client):
    """New entries added between page fetches don't disrupt the current pagination session."""
    now = dt.datetime.now(dt.timezone.utc)
    per_page = app.config["ENTRY_PAGE_SIZE"]

    items = [{"title": f"initial-a{i}", "date": now - dt.timedelta(hours=3, minutes=i)} for i in range(per_page + 2)]
    create_feed(client, "feed1.com", items)

    response = client.get("/")
    assert "initial-a0" in response.text
    next_page = re.search(r'page=([^&"]+)', response.text).group(1)

    # New entries arrive between pages
    create_feed(client, "feed2.com", [{"title": "new-entry", "date": now - dt.timedelta(minutes=1)}])

    response = client.get(f"/?page={next_page}")
    assert "new-entry" not in response.text
    assert f"initial-a{per_page}" in response.text

    # Fresh session includes the new entry
    response = client.get("/")
    assert "new-entry" in response.text


def test_favorites(client):
    feed_domain = "feed1.com"
    response, _feed_id = create_feed(
        client,
        feed_domain,
        [
            {"title": "my-third-article", "date": "2023-11-10 00:00Z"},
            {"title": "my-second-article", "date": "2023-10-10 00:00Z"},
            {"title": "my-first-article", "date": "2023-10-01 00:00Z"},
        ],
    )
    entry_ids = extract_entry_ids(response)

    # pin the 3rd, then the 2nd
    response = client.put(f"/favorites/{entry_ids[0]}")
    assert response.status_code == 204
    response = client.put(f"/favorites/{entry_ids[1]}")
    assert response.status_code == 204

    response = client.get("/favorites")
    assert "my-first-article" not in response.text
    assert "my-second-article" in response.text
    assert "my-third-article" in response.text

    # 2nd appears first because most recent fav, even though it's older
    assert response.text.find("my-second-article") < response.text.find("my-third-article")


def test_pinned(client):
    response, _feed_id = create_feed(
        client,
        "feed1.com",
        [{"title": "f1-a1", "date": "2023-10-01 00:00Z"}, {"title": "f1-a2", "date": "2023-10-10 00:00Z"}],
        folder="folder1",
    )
    f1a2_pin_url = re.search(r"/pinned/(\d+)", response.text).group(0)

    response, _ = create_feed(
        client,
        "feed2.com",
        [{"title": "f2-a1", "date": "2023-10-01 00:00Z"}, {"title": "f2-a2", "date": "2023-10-10 00:00Z"}],
    )
    f2_a2_pin_url = re.search(r"/pinned/(\d+)", response.text).group(0)

    response = client.get("/")
    assert "f1-a2" in response.text
    assert "f2-a2" in response.text
    response = client.get("/folder/folder1")
    assert "f1-a2" in response.text
    assert "f2-a2" not in response.text

    # add some pages of more entries in both feeds, to ensure the older ones are pushed out of the page
    now = dt.datetime.now(dt.timezone.utc)
    for i in range(1, 20):
        date = now - dt.timedelta(hours=1, minutes=1)
        create_feed(client, f"f{i}-folder1.com", [{"title": "article1", "date": date}], folder="folder1")

    # verify the old entries where pushed out of home and folder
    response = client.get("/")
    assert "f1-a2" not in response.text
    assert "f2-a2" not in response.text
    response = client.get("/folder/folder1")
    assert "f1-a2" not in response.text

    # pin the old entries
    response = client.put(f1a2_pin_url)
    assert response.status_code == 200
    response = client.put(f2_a2_pin_url)
    assert response.status_code == 200

    # verify they are pinned to the home and folder
    response = client.get("/")
    assert "f1-a2" in response.text
    assert "f2-a2" in response.text
    response = client.get("/folder/folder1")
    assert "f1-a2" in response.text
    assert "f2-a2" not in response.text


def test_entries_not_mixed_between_users(app, client):
    "Users only see entries from their own feeds."
    create_feed(client, "feed1.com", [{"title": "user1-article", "date": "2023-10-01 00:00Z"}])

    email2 = create_user(app)
    client2 = app.test_client()
    client2.post("/auth/login", data={"email": email2, "password": "password"}, follow_redirects=True)
    create_feed(client2, "feed2.com", [{"title": "user2-article", "date": "2023-10-01 00:00Z"}])

    response = client.get("/")
    assert "user1-article" in response.text
    assert "user2-article" not in response.text

    response = client2.get("/")
    assert "user2-article" in response.text
    assert "user1-article" not in response.text


def test_view_entry_content(client):
    # create feed with a sample entry
    with open("tests/sample.html") as sample:
        body = sample.read()
    response, _ = create_feed(
        client,
        "olano.dev",
        [
            {
                "title": "reclaiming-the-web",
                "date": "2023-12-12T00:00:00-03:00",
                "description": "short content",
                "body": body,
            }
        ],
    )
    assert "reclaiming-the-web" in response.text
    assert "short content" in response.text
    entry_url = re.search(r"/entries/(\d+)", response.text).group(0)
    response = client.get(entry_url)

    assert "reclaiming-the-web" in response.text
    assert "I had some ideas of what I wanted" in response.text


def test_add_external_entry(client):
    with open("tests/sample.html") as sample:
        body = sample.read()
    content_url = "http://olano.dev/reclaiming-the-web"
    mock_request(content_url, body=body)

    # add a standalone entry for that url, check that browser redirects to view content
    response = client.post("/entries/", query_string={"url": content_url, "redirect": 1}, follow_redirects=True)
    assert response.status_code == 200
    assert "reclaiming-the-web" in response.text
    assert "I had some ideas of what I wanted" in response.text

    # add same url again, verify that redirected entry url is the same as before
    previous_entry_url = response.request.path
    response = client.post("/entries/", query_string={"url": content_url, "redirect": 1}, follow_redirects=True)
    assert response.status_code == 200
    assert response.request.path == previous_entry_url

    # check that standalone entry appears in feed
    client.post("/session/hide_seen")
    response = client.get("/")
    assert "reclaiming-the-web" in response.text
    # short content taken from page meta description
    assert "There’s a kind of zen flow" in response.text


def test_discover_feed(client):
    "The add feed page discovers RSS feeds from website URLs and pre-fills the form."
    # Case 1: direct feed URL — form shows the same URL back
    mock_feed("sample-blog.com", [{"title": "article-1", "date": "2023-10-01 00:00Z"}])
    feed_url = "http://sample-blog.com/feed"
    response = client.get(f"/feeds/new?url={feed_url}")
    assert response.status_code == 200
    assert feed_url in response.text

    # Case 2: HTML page with RSS link tag — feed URL extracted and shown
    # mock_feed registers the RSS at example-blog.com/feed; override the base URL with an HTML link page
    mock_feed("example-blog.com", [{"title": "article-1", "date": "2023-10-01 00:00Z"}])
    site_url = "http://example-blog.com"
    mock_request(site_url, body=f'<html><head><link type="application/rss+xml" href="{site_url}/feed"></head></html>')
    response = client.get(f"/feeds/new?url={site_url}")
    assert response.status_code == 200
    assert f"{site_url}/feed" in response.text

    # Case 3: no feed found — error message shown
    no_feed_url = "http://no-feed-site.com"
    mock_request(no_feed_url, body="<html><body>nothing here</body></html>")
    # mock the common paths so discover_feed doesn't raise on unmocked requests
    for path in ["/feed", "/rss", "/feed.xml", "/rss.xml"]:
        mock_request(f"{no_feed_url}{path}", body="not xml")
    response = client.get(f"/feeds/new?url={no_feed_url}")
    assert response.status_code == 200
    assert "not found" in response.text.lower()


def test_feed_list(client):
    "The feed management page lists all feeds for the current user."
    create_feed(client, "feed1.com", [{"title": "a1", "date": "2023-10-01 00:00Z"}])
    create_feed(client, "feed2.com", [{"title": "a2", "date": "2023-10-01 00:00Z"}], folder="tech")

    response = client.get("/feeds")
    assert response.status_code == 200
    assert "feed1.com" in response.text
    assert "feed2.com" in response.text


def test_feed_edit(client):
    "Feed metadata (name, folder) can be updated through the edit form."
    _, feed_id = create_feed(client, "myfeed.com", [{"title": "a1", "date": "2023-10-01 00:00Z"}])

    response = client.get(f"/feeds/{feed_id}")
    assert response.status_code == 200
    assert "myfeed.com" in response.text

    feed_url = "http://myfeed.com/feed"
    response = client.post(
        f"/feeds/{feed_id}",
        data={"name": "renamed-feed", "url": feed_url},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert response.request.path == "/feeds"
    assert "renamed-feed" in response.text


def test_feed_delete(client):
    response, feed_id = create_feed(
        client,
        "feed1.com",
        [
            {"title": "pin-entry", "date": "2023-12-01 00:00Z"},
            {"title": "fav-entry", "date": "2023-11-10 00:00Z"},
            {"title": "plain-entry", "date": "2023-10-10 00:00Z"},
        ],
    )

    response = client.get("/")
    assert "pin-entry" in response.text
    assert "fav-entry" in response.text
    assert "plain-entry" in response.text

    # check the 3 appear when requesting by feed name
    response = client.get(f"/feeds/{feed_id}/entries")
    assert "pin-entry" in response.text
    assert "fav-entry" in response.text
    assert "plain-entry" in response.text

    # pin the 1st, fav the 2nd
    entry_ids = extract_entry_ids(response)
    response = client.put("/pinned/" + entry_ids[0])
    assert response.status_code == 200

    response = client.put("/favorites/" + entry_ids[1])
    assert response.status_code == 204

    # delete the feed
    response = client.delete(f"/feeds/{feed_id}")
    assert response.status_code == 204

    # check pinned and favorited appear on home, the other is deleted
    response = client.get("/")
    assert "pin-entry" in response.text
    assert "fav-entry" in response.text
    assert "plain-entry" not in response.text

    # check empty when requesting by feed name
    response = client.get(f"/feeds/{feed_id}/entries")
    assert "pin-entry" not in response.text
    assert "fav-entry" not in response.text
    assert "plain-entry" not in response.text


def test_feed_filters(client):
    "Entries not matching the feed's filter expression are excluded when syncing."
    feed_url = mock_feed("blog.com", [
        {"title": "alice-post", "date": "2023-10-01 00:00Z"},
        {"title": "bob-post", "date": "2023-10-01 00:00Z"},
    ])
    response = client.post(
        "/feeds/new",
        data={"type": "rss", "name": "blog.com", "url": feed_url, "filters": "title=alice"},
        follow_redirects=True,
    )
    assert "alice-post" in response.text
    assert "bob-post" not in response.text


def test_text_search(client):
    "A text search query returns only entries whose title or content contains the search term."
    create_feed(client, "feed1.com", [
        {"title": "python-tutorial", "date": "2023-10-01 00:00Z"},
        {"title": "javascript-guide", "date": "2023-10-01 00:00Z"},
    ])

    response = client.get("/?q=python")
    assert "python-tutorial" in response.text
    assert "javascript-guide" not in response.text


def test_entry_unpin(client):
    "Unpinning a pinned entry removes it from the pinned section of the home feed."
    response, _ = create_feed(client, "feed1.com", [{"title": "my-article", "date": "2023-10-01 00:00Z"}])
    pin_url = re.search(r"/pinned/(\d+)", response.text).group(0)

    # entry_pin returns the updated pinned list as a partial
    response = client.put(pin_url)
    assert "my-article" in response.text

    response = client.put(pin_url)
    assert "my-article" not in response.text


def test_unfavorite(client):
    "Favoriting an already-favorited entry removes it from the favorites list."
    response, _ = create_feed(client, "feed1.com", [{"title": "my-article", "date": "2023-10-01 00:00Z"}])
    entry_id = extract_entry_ids(response)[0]

    client.put(f"/favorites/{entry_id}")
    assert "my-article" in client.get("/favorites").text

    client.put(f"/favorites/{entry_id}")
    assert "my-article" not in client.get("/favorites").text


def test_feed_name_conflict(client):
    "Attempting to add a feed with a name that already exists shows an error and creates no duplicate."
    create_feed(client, "feed1.com", [{"title": "a1", "date": "2023-10-01 00:00Z"}])

    feed_url = mock_feed("other.com", [{"title": "b1", "date": "2023-10-01 00:00Z"}])
    response = client.post(
        "/feeds/new",
        data={"type": "rss", "name": "feed1.com", "url": feed_url},
        follow_redirects=True,
    )
    assert "already exists" in response.text
    assert "b1" not in client.get("/").text


def test_entry_security_isolation(app, client):
    "A user cannot access or modify entries belonging to another user."
    response, _ = create_feed(client, "feed1.com", [{"title": "user1-article", "date": "2023-10-01 00:00Z"}])
    entry_id = extract_entry_ids(response)[0]

    email2 = create_user(app)
    client2 = app.test_client()
    client2.post("/auth/login", data={"email": email2, "password": "password"}, follow_redirects=True)

    assert client2.get(f"/entries/{entry_id}").status_code == 404
    assert client2.put(f"/favorites/{entry_id}").status_code == 404
    assert client2.put(f"/pinned/{entry_id}").status_code == 404
