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
    return parts.netloc.strip('www.')


@app.template_filter('sanitize')
def sanitize_content(html):
    # poor man's line truncating: reduce the amount of characters and let bs4 fix the html
    if len(html) > 500:
        html = html[:500] + '…'
        html = str(BeautifulSoup(html, 'lxml'))
    return html
