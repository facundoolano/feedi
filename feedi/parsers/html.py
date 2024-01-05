import datetime
import json

import dateparser
from bs4 import BeautifulSoup
from feedi import scraping
from feedi.requests import requests


def fetch(url):
    """
    Return the entry values for an article at the given url.
    Raises ValueError if the url doesn't seem to point to an article (it doesn't have a title).
    Raises HTTPError if the request is not successfull.
    """

    response = requests.get(url)
    response.raise_for_status()

    if not response.ok:
        raise Exception()

    soup = BeautifulSoup(response.content, 'lxml')
    metadata = scraping.all_meta(soup)

    title = metadata.get('og:title', metadata.get('twitter:title'))
    if not title and soup.title:
        raise ValueError(f"{url} is missing article metadata")

    if 'og:article:published_time' in metadata:
        display_date = dateparser.parse(metadata['og:article:published_time'])
    else:
        display_date = datetime.datetime.utcnow()

    username = metadata.get('author', '').split(',')[0]

    entry = {
        'remote_id': url,
        'title': title,
        'username': username,
        'display_date': display_date,
        'sort_date': datetime.datetime.utcnow(),
        'content_short': metadata.get('og:description', metadata.get('description')),
        'media_url': metadata.get('og:image', metadata.get('twitter:image')),
        'target_url': url,
        'content_url': url,
        'raw_data': json.dumps(metadata)
    }

    return entry
