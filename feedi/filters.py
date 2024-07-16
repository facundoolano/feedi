# coding: utf-8
import datetime
import urllib

import flask
from bs4 import BeautifulSoup
from flask import current_app as app


# TODO unit test this
@app.template_filter('humanize')
def humanize_date(dt):
    delta = datetime.datetime.utcnow() - dt

    if delta < datetime.timedelta(seconds=60):
        return f"{delta.seconds}s"
    elif delta < datetime.timedelta(hours=1):
        return f"{delta.seconds // 60}m"
    elif delta < datetime.timedelta(days=1):
        return f"{delta.seconds // 60 // 60}h"
    elif delta < datetime.timedelta(days=8):
        return f"{delta.days}d"
    elif delta < datetime.timedelta(days=365):
        return dt.strftime("%b %d")
    return dt.strftime("%b %d, %Y")


@app.template_filter('url_domain')
def feed_domain(url):
    parts = urllib.parse.urlparse(url)
    return parts.netloc.replace('www.', '')


@app.template_filter('should_unfold_folder')
def should_unfold_folder(filters, folder_name, folder_feeds):
    if filters.get('folder') == folder_name:
        return True

    if filters.get('feed_name'):
        if filters['feed_name'] in [f.name for f in folder_feeds]:
            return True

    return False


@app.template_filter('contains_feed_name')
def contains_feed_name(feed_list, selected_name):
    for feed in feed_list:
        if feed.name == selected_name:
            return True
    return False


@app.template_filter('sanitize')
def sanitize_content(html, truncate=True):
    if not html:
        return ''

    # poor man's line truncating: reduce the amount of characters and let bs4 fix the html
    soup = BeautifulSoup(html, 'lxml')
    if len(html) > 500 and truncate:
        html = html[:500] + '…'
        soup = BeautifulSoup(html, 'lxml')

    if soup.html:
        if soup.html.body:
            soup.html.body.unwrap()
        soup.html.unwrap()

    for a in soup.find_all('a', href=True):
        # prevent link clicks triggering the container's click event
        # add kb modifiers to open in reader
        read_url = flask.url_for("entry_add", url=a["href"], redirect=1)
        a['_'] = f"""
        on click[shiftKey and not metaKey] go to url {read_url} then halt
        then on click[shiftKey and metaKey] go to url {read_url} in new window then halt
        then on click halt the event's bubbling
        """

    return str(soup)


# FIXME this wouldn't be necessary if I could figure out the proper CSS
# to make the text hide on overflow
@app.template_filter('entry_excerpt')
def entry_excerpt(entry):
    if not entry.content_short:
        return '[click to read]'

    if entry.content_url and entry.title:
        title = entry.title
    elif entry.has_distinct_user:
        title = entry.display_name or entry.username
    else:
        title = entry.feed.name

    body_text = BeautifulSoup(entry.content_short, 'lxml').text

    # truncate according to display title length so all entries
    # have aproximately the same length
    max_length = 100
    max_body_length = max(0, max_length - len(title))
    if len(body_text) > max_body_length:
        return body_text[:max_body_length] + '…'

    return body_text
