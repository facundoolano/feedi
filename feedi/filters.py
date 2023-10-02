# coding: utf-8
import datetime
import urllib

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
        return f"{delta.seconds // 60 // 60 }h"
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
def sanitize_content(html):
    # poor man's line truncating: reduce the amount of characters and let bs4 fix the html
    soup = BeautifulSoup(html, 'lxml')
    if len(html) > 500:
        html = html[:500] + '…'
        soup = BeautifulSoup(html, 'lxml')

    if soup.html:
        if soup.html.body:
            soup.html.body.unwrap()
        soup.html.unwrap()

    return str(soup)


@app.template_filter('excerpt')
def sanitize_content(html):
    # poor man's line truncating: reduce the amount of characters and let bs4 fix the html
    text = BeautifulSoup(html, 'lxml').text
    if len(text) > 50:
        text = text[:50] + '…'
    return text
