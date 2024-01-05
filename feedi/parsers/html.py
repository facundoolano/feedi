import datetime

import dateparser
from bs4 import BeautifulSoup
from feedi import scraping
from feedi.requests import requests


def fetch(url):
    "Return the entry values for an article at the given url."

    response = requests.get(url)
    soup = BeautifulSoup(response.content, 'lxml')

    published = scraping.extract_meta(soup, 'og:article:published_time')
    if published:
        display_date = dateparser.parse(published)
    else:
        display_date = datetime.datetime.utcnow()

    # FIXME consider skipping if no title? and no og:type=article?
    title = scraping.extract_meta(soup, 'og:title', 'twitter:title')
    if not title and soup.title:
        title = soup.title.text

    username = scraping.extract_meta(soup, 'author') or ''
    username = username.split(',')[0]

    entry = {
        'remote_id': url,
        'title': title,
        'username': username,
        'display_date': display_date,
        'sort_date': datetime.datetime.utcnow(),
        'content_short': scraping.extract_meta(soup, 'og:description', 'description'),
        'media_url': scraping.extract_meta(soup, 'og:image', 'twitter:image'),
        'target_url': url,
        'content_url': url,
    }

    # TODO make raw entry the set of met tags

    return entry
