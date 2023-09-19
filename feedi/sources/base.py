import datetime
import logging
import traceback
import urllib

import favicon
from bs4 import BeautifulSoup
from feedi.requests import requests

logger = logging.getLogger(__name__)


class BaseParser:
    """
    Abstract class with base parsing logic to produce a list of entry values from a
    remote resource. The actual fetching and field parsing is to be defined by subclasses.
    """

    FIELDS = ['title', 'avatar_url', 'username', 'body',
              'media_url', 'remote_id', 'remote_created', 'remote_updated', 'entry_url', 'content_url']

    @staticmethod
    def detect_feed_icon(url):
        # strip path
        url_parts = urllib.parse.urlparse(url)
        url = f'{url_parts.scheme}://{url_parts.netloc}'
        try:
            favicons = favicon.get(url)
        except:
            logger.exception("error fetching favicon: %s", url)
            return
        favicons = [f for f in favicons if f.height == f.width]
        if not favicons:
            logger.debug("no feed icon found: %s", favicons)
            return
        icon_url = favicons[0].url
        logger.debug('using favicon %s', icon_url)

        return icon_url

    def __init__(self, feed_name):
        self.feed_name = feed_name
        self.response_cache = {}

    def fetch(self, _feed_url):
        """
        To be implemented by subclasses, fetch the feed entries from the given url
        and return a (feed_metadata, [item_data]) tuple.
        """
        raise NotImplementedError

    def parse(self, entry, previous_fetch, skip_older_than):
        """
        Given an entry raw data (as produced by the `fetch` method) and parse
        into a dictionary by using parse_* methods on each of the `cls.FIELDS`
        names.
        """
        result = {}

        try:
            url = self.parse_entry_url(entry)
            published = self.parse_remote_created(entry)
            updated = self.parse_remote_updated(entry)
        except Exception as error:
            exc_desc_lines = traceback.format_exception_only(type(error), error)
            exc_desc = ''.join(exc_desc_lines).rstrip()
            logger.error("skipping errored entry %s %s",
                         self.feed_name, exc_desc)
            return

        # don't try to process stuff that hasn't changed recently
        if previous_fetch and updated < previous_fetch:
            logger.debug('skipping up to date entry %s', entry.get('link'))
            return

        # or that is too old
        if (skip_older_than and published and
                datetime.datetime.utcnow() - published > datetime.timedelta(days=skip_older_than)):
            logger.debug('skipping old entry %s %s', self.feed_name, url)
            return

        for field in self.FIELDS:
            method = 'parse_' + field
            try:
                result[field] = getattr(self, method)(entry)
            except Exception as error:
                exc_desc_lines = traceback.format_exception_only(type(error), error)
                exc_desc = ''.join(exc_desc_lines).rstrip()
                logger.error("skipping errored entry %s %s %s",
                             self.feed_name,
                             url,
                             exc_desc)
                return

        return result

    # TODO make this a proper cache of any sort of request, and cache all.
    def request(self, url):
        """
        GET the content of the given url, and if the response is succesful
        cache it for subsequent calls to this method.
        """
        if url in self.response_cache:
            logger.debug("using cached response %s", url)
            return self.response_cache[url]

        logger.debug("making request %s", url)
        content = requests.get(url).content
        self.response_cache[url] = content
        return content

    def fetch_meta(self, url, tag):
        """
        GET the body of the url (which could be already cached) and extract the content of the given meta tag.
        """
        # TODO try accepting a series of tags to try in turn
        soup = BeautifulSoup(self.request(url), 'lxml')
        return extract_meta(soup, tag)


# FIXME duplicated
def extract_meta(soup, tag):
    meta_tag = soup.find("meta", property=tag, content=True)
    if meta_tag:
        return meta_tag['content']
