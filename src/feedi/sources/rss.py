import datetime
import json
import pprint
import time

import favicon
import feedparser
import requests
from bs4 import BeautifulSoup


# FIXME this shouldn't receive a logger
def fetch(logger, url, previous_fetch, skip_older_than, etag=None, modified=None):
    # using standard feed headers to prevent re-fetching unchanged feeds
    # https://feedparser.readthedocs.io/en/latest/http-etag.html
    feed = feedparser.parse(url, etag=etag, modified=modified)

    if not feed['feed']:
        logger.info('skipping empty feed %s %s', url, feed.get('debug_message'))
        return [], None, None, None

    # also checking with the internal updated field in case feed doesn't support the standard headers
    if previous_fetch and 'updated_parsed' in feed and to_datetime(feed['updated_parsed']) < previous_fetch:
        logger.info('skipping up to date feed %s', url)
        return [], None, None, None

    parser_cls = BaseParser
    # FIXME this is hacky, we aren't enforcing an order which may be necessary
    for cls in BaseParser.__subclasses__():
        if cls.is_compatible(url, feed):
            parser_cls = cls
            break
    parser = parser_cls(feed, logger)

    logger.info('parsing %s with %s', url, parser_cls)
    return parser.parse(previous_fetch, skip_older_than), feed['feed'], getattr(feed, 'etag', None), getattr(feed, 'modified', None)


class BaseParser:
    """
    TODO
    """

    FIELDS = ['title', 'avatar_url', 'username', 'body',
              'media_url', 'remote_id', 'remote_created', 'remote_updated', 'entry_url', 'content_url']

    @staticmethod
    def is_compatible(_feed_url, _feed_data):
        """
        Returns whether this class knows how to parse entries from the given feed.
        The base parser should reasonably work with any rss feed.
        """
        # subclasses need to override this. This base class can be used directly without testing for compatibility
        raise NotImplementedError

    # TODO the logger should be inferred from the module, not passed as arg
    def __init__(self, feed, logger):
        self.feed = feed
        self.logger = logger
        self.response_cache = {}

    def parse(self, previous_fetch, skip_older_than):
        """
        TODO
        """
        for entry in self.feed['entries']:
            if 'link' not in entry or 'summary' not in entry:
                self.logger.warn(f"entry seems malformed {entry}")
                continue

            # again, don't try to process stuff that hasn't changed recently
            if previous_fetch and 'updated_parsed' in entry and to_datetime(entry['updated_parsed']) < previous_fetch:
                self.logger.debug('skipping up to date entry %s', entry['link'])
                continue

            # or that is too old
            if 'published_parsed' in entry and datetime.datetime.now() - to_datetime(entry['published_parsed']) > datetime.timedelta(days=skip_older_than):
                self.logger.debug('skipping old entry %s', entry['link'])
                continue

            result = {
                'raw_data': json.dumps(entry)
            }

            try:
                for field in self.FIELDS:
                    method = 'parse_' + field
                    result[field] = getattr(self, method)(entry)
            except ValueError:
                self.logger.exception("skipping errored entry")
                continue

            yield result

    def parse_title(self, entry):
        return entry['title']

    def parse_content_url(self, entry):
        return entry['link']

    def parse_entry_url(self, entry):
        return self.parse_content_url(entry)

    def parse_username(self, entry):
        return entry.get('author')

    def parse_avatar_url(self, entry):
        url = entry.get('source', {}).get('icon')
        if url and requests.head(url).ok:
            self.logger.debug('found entry-level avatar %s', url)
            return url

    def parse_body(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')

        # remove images in case there are any inside a paragraph
        for tag in soup('img'):
            tag.decompose()
        # return the rest of the html untouched, assuming any truncating will be done
        # on the view side if necessary (so it applies regardless of the parser implementation)
        return str(soup)

    def parse_media_url(self, entry):
        # first try to get it in standard feed fields
        if 'media_thumbnail' in entry:
            return entry['media_thumbnail'][0]['url']

        if 'media_content' in entry and entry['media_content'][0].get('type') == 'image':
            return entry['media_content'][0]['url']

        # else try to extract it from the summary html
        soup = BeautifulSoup(entry['summary'], 'lxml')
        if soup.img:
            return soup.img['src']

        parsed_dest_url = self.parse_content_url(entry)
        return self.fetch_meta(parsed_dest_url, "og:image") or self.fetch_meta(parsed_dest_url, "twitter:image")

    def parse_remote_id(self, entry):
        return entry.get('id', entry['link'])

    def parse_remote_created(self, entry):
        dt = to_datetime(entry['published_parsed'])
        if dt > datetime.datetime.utcnow():
            raise ValueError(f"publication date is in the future {entry}")
        return dt

    def parse_remote_updated(self, entry):
        dt = to_datetime(entry['updated_parsed'])
        if dt > datetime.datetime.utcnow():
            raise ValueError("publication date is in the future")
        return dt

    # FIXME make this a proper cache of any sort of request, and cache all.
    def request(self, url):
        if url in self.response_cache:
            self.logger.debug("using cached response %s", url)
            return self.response_cache[url]

        self.logger.debug("making request %s", url)
        content = requests.get(url).content
        self.response_cache[url] = content
        return content

    def fetch_meta(self, url, tag):
        """
        TODO
        """
        soup = BeautifulSoup(self.request(url), 'lxml')
        meta_tag = soup.find("meta", property=tag, content=True)
        if meta_tag:
            return meta_tag['content']


# FIXME reduce duplication between aggregators
class RedditParser(BaseParser):
    def is_compatible(_feed_url, feed_data):
        return 'reddit.com' in feed_data['feed']['link']

    def parse_body(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')
        link_url = soup.find("a", string="[link]")
        comments_url = soup.find("a", string="[comments]")

        if link_url['href'] == comments_url['href']:
            # this looks like it's a local reddit discussion
            # return the summary instead of fetching description

            # remove the links from the body first
            link_url.decompose()
            comments_url.decompose()
            return str(soup)

        return (self.fetch_meta(link_url, 'og:description') or
                self.fetch_meta(link_url, 'description'))

    def parse_content_url(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')
        return soup.find("a", string="[link]")['href']

    def parse_entry_url(self, entry):
        # this particular feed puts the reddit comments page in the link
        return entry['link']


class LobstersParser(BaseParser):
    def is_compatible(_feed_url, feed_data):
        return 'lobste.rs' in feed_data['feed']['link']

    def parse_body(self, entry):
        # skip link-only posts
        if 'Comments' in entry['summary']:
            url = self.parse_content_url(entry)
            return (self.fetch_meta(url, 'og:description') or
                    self.fetch_meta(url, 'description'))
        return entry['summary']

    def parse_entry_url(self, entry):
        if 'Comments' in entry['summary']:
            soup = BeautifulSoup(entry['summary'], 'lxml')
            return soup.find("a", string="Comments")['href']
        return entry['link']


class HackerNewsParser(BaseParser):
    def is_compatible(_feed_url, feed_data):
        return 'news.ycombinator.com' in feed_data['feed']['link']

    def parse_body(self, entry):
        # skip link-only posts
        if 'Article URL' in entry['summary']:
            url = self.parse_content_url(entry)
            return (self.fetch_meta(url, 'og:description') or self.fetch_meta(url, 'description'))
        return entry['summary']

    def parse_entry_url(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')
        return soup.find(lambda tag: tag.name == 'p' and 'Comments URL' in tag.text).a['href']


class MastodonUserParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(_feed_url, feed_data):
        return 'mastodon' in feed_data['feed'].get('generator', '').lower()

    def parse_title(self, _entry):
        return self.feed['feed']['title']


class GithubFeedParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(feed_url, _feed_data):
        return 'github.com' in feed_url and 'private.atom' in feed_url

    def parse_body(self, _entry):
        return None

    def parse_avatar_url(self, entry):
        return entry['media_thumbnail'][0]['url']

    def parse_media_url(self, _entry):
        return None

    def parse_entry_url(self, _entry):
        return None

    def parse_content_url(self, _entry):
        return None


class GoodreadsFeedParser(BaseParser):
    """
    TODO
    """
    @staticmethod
    def is_compatible(feed_url, _feed_data):
        return 'goodreads.com' in feed_url and '/home/index_rss' in feed_url

    def parse_body(self, _entry):
        return None

    def parse_media_url(self, _entry):
        return None


def detect_feed_icon(app, url):
    feed = feedparser.parse(url)
    icon_url = feed['feed'].get('icon', feed['feed'].get('webfeeds_icon'))
    if icon_url and requests.head(icon_url).ok:
        app.logger.debug("using feed icon: %s", icon_url)
    else:
        favicons = favicon.get(feed['feed'].get('link', url))
        # if multiple formats, assume the .ico is the canonical one if present
        favicons = [f for f in favicons if f.height == f.width] or favicons
        icon_url = favicons[0].url
        app.logger.debug('using favicon %s', icon_url)

    return icon_url

def pretty_print(url):
    feed = feedparser.parse(url)
    pp = pprint.PrettyPrinter(depth=10)
    pp.pprint(feed)

def to_datetime(struct_time):
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))
