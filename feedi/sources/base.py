import datetime
import logging
import traceback

import favicon
from bs4 import BeautifulSoup
from feedi.requests import requests

logger = logging.getLogger(__name__)


class BaseParser:
    """
    TODO
    """

    FIELDS = ['title', 'avatar_url', 'username', 'body',
              'media_url', 'remote_id', 'remote_created', 'remote_updated', 'entry_url', 'content_url']

    @staticmethod
    def is_compatible(_feed_url):
        """
        # FIXME update
        Returns whether this class knows how to parse entries from the given feed.
        The base parser should reasonably work with any rss feed.
        """
        # subclasses need to override this. This base class can be used directly without testing for compatibility
        raise NotImplementedError

    @staticmethod
    def detect_feed_icon(url):
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

    def __init__(self, feed_url, feed_name):
        self.feed_url = feed_url
        self.feed_name = feed_name
        self.response_cache = {}

    def fetch(self):
        # TODO add doc.
        # fetch with self.feed_url
        # TODO returns (new_metadata, [item_data])
        raise NotImplementedError

    def parse(self, entry, previous_fetch, skip_older_than):
        """
        FIXME
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

     # FIXME make this a proper cache of any sort of request, and cache all.
    def request(self, url):
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
