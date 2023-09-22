import datetime
import logging
import pprint
import time
import urllib

import feedparser
from bs4 import BeautifulSoup
from feedi.requests import USER_AGENT, requests
from feedi.sources.base import BaseParser

logger = logging.getLogger(__name__)

feedparser.USER_AGENT = USER_AGENT


def get_best_parser(url):
    # Try with all the customized parsers, and if none is compatible default to the generic RSS parsing.
    for cls in RSSParser.__subclasses__():
        if cls.is_compatible(url):
            return cls
    return RSSParser


class RSSParser(BaseParser):
    """
    A generic parser for RSS articles.
    """

    @staticmethod
    def is_compatible(_feed_url):
        """
        To be overridden by subclasses, this method inspects the url to decide if a given parser
        class is suited to parse the source at the given url.
        """
        raise NotImplementedError

    @staticmethod
    def detect_feed_icon(url):
        # try to get the icon from an rss field
        feed = feedparser.parse(url)
        icon_url = feed['feed'].get('icon', feed['feed'].get('webfeeds_icon'))
        if icon_url and requests.head(icon_url).ok:
            logger.debug("using feed icon: %s", icon_url)
            return icon_url

        # if not found default to favicon from url domain

        return BaseParser.detect_feed_icon(url)

    def fetch(self, feed_url, previous_fetch, etag, modified, filters=None):
        # using standard feed headers to prevent re-fetching unchanged feeds
        # https://feedparser.readthedocs.io/en/latest/http-etag.html
        feed = feedparser.parse(feed_url, etag=etag, modified=modified)

        if not feed['feed']:
            logger.info('skipping empty feed %s %s', feed_url, feed.get('debug_message'))
            return None, [], None, None

        # also checking with the internal updated field in case feed doesn't support the standard headers
        if previous_fetch and 'updated_parsed' in feed and to_datetime(feed['updated_parsed']) <= previous_fetch:
            logger.info('skipping up to date feed %s', feed_url)
            return None, [], None, None

        etag = getattr(feed, 'etag', None)
        modified = getattr(feed, 'modified', None)
        items = [i for i in feed['items'] if self._matches(i, filters)]
        return feed['feed'], items, etag, modified

    @staticmethod
    def _matches(entry, filters):
        """
        Check a filter expression (e.g. "author=John Doe") against the parsed entry and return whether it matches the condition.
        """
        # this is very brittle and ad hoc but gets the job done
        if not filters:
            return True
        filters = filters.split(',')
        for filter in filters:
            field, value = filter.strip().split('=')
            field = field.lower().strip()
            value = value.lower().strip()

            if not value in entry.get(field, '').lower():
                return False

        return True

    def parse_title(self, entry):
        return entry['title']

    def parse_content_url(self, entry):
        return entry['link']

    def parse_entry_url(self, entry):
        return self.parse_content_url(entry)

    def parse_username(self, entry):
        # TODO if missing try to get from meta?
        return entry.get('author')

    def parse_avatar_url(self, entry):
        url = entry.get('source', {}).get('icon')
        if url and requests.head(url).ok:
            logger.debug('found entry-level avatar %s', url)
            return url

    def parse_body(self, entry):
        if not 'summary' in entry:
            # TODO could alternatively fetch and get summary from meta or the first paragraph
            return None

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
        if 'summary' in entry:
            soup = BeautifulSoup(entry['summary'], 'lxml')
            if soup.img:
                return soup.img['src']

        parsed_dest_url = self.parse_content_url(entry)
        return self.fetch_meta(parsed_dest_url, "og:image") or self.fetch_meta(parsed_dest_url, "twitter:image")

    def parse_remote_id(self, entry):
        return entry.get('id', entry['link'])

    def parse_remote_created(self, entry):
        dt = to_datetime(entry.get('published_parsed', entry.get('updated_parsed')))
        if dt > datetime.datetime.utcnow():
            raise ValueError(f"publication date is in the future {dt}")
        return dt

    def parse_remote_updated(self, entry):
        dt = to_datetime(entry['updated_parsed'])
        if dt > datetime.datetime.utcnow():
            raise ValueError("publication date is in the future")
        return dt

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


# FIXME reduce duplication between aggregators
class RedditParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'reddit.com' in feed_url

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


class LobstersParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'lobste.rs' in feed_url

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

    def parse_username(self, entry):
        username = super().parse_username(entry)
        return username.split('@')[0]


class HackerNewsParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'news.ycombinator.com' in feed_url or 'hnrss.org' in feed_url

    def parse_body(self, entry):
        # skip link-only posts
        if 'Article URL' in entry['summary']:
            url = self.parse_content_url(entry)
            return (self.fetch_meta(url, 'og:description') or self.fetch_meta(url, 'description'))
        return entry['summary']

    def parse_entry_url(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')
        return soup.find(lambda tag: tag.name == 'p' and 'Comments URL' in tag.text).a['href']


class GithubFeedParser(RSSParser):
    """
    Parser for the personal Github notifications feed.
    """
    @staticmethod
    def is_compatible(feed_url):
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
        # don't open this in the local reader
        return None


class GoodreadsFeedParser(RSSParser):
    """
    Parser for the Goodreads private home rss feed.
    """
    @staticmethod
    def is_compatible(feed_url):
        return 'goodreads.com' in feed_url and '/home/index_rss' in feed_url

    def parse_body(self, _entry):
        return None

    def parse_media_url(self, _entry):
        return None

    def parse_entry_url(self, entry):
        return entry['link']

    def parse_content_url(self, _entry):
        # don't open this in the local reader
        return None


class EconomistParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'economist.com' in feed_url

    def parse_content_url(self, entry):
        # the feed entry link is garbage, get it from the summary html
        soup = BeautifulSoup(entry['summary'], 'lxml')
        return soup.find("a", href=True)['href']

    def parse_body(self, entry):
        url = self.parse_content_url(entry)
        return (self.fetch_meta(url, 'og:description') or self.fetch_meta(url, 'description'))


# TODO unit test
def discover_feed(url):
    """
    Given a website URL, try to discover the first rss/atom feed url in it
    and return it along the feed title.
    """
    res = requests.get(url)
    if not res.ok:
        logger.warn("Failed to discover feed from url %s %s", url, res)
        return

    soup = BeautifulSoup(res.content, 'lxml')

    # resolve title
    title = extract_meta(soup, 'og:site_name') or extract_meta(
        soup, 'og:title')
    if not title:
        title = soup.find('title')
        if title:
            title = title.text

    link_types = ["application/rss+xml",
                  "application/atom+xml",
                  "application/x.atom+xml",
                  "application/x-atom+xml"]

    feed_url = None
    # first try with the common link tags for feeds
    for type in link_types:
        link = soup.find('link', type=type, href=True)
        if link:
            feed_url = make_absolute(url, link['href'])
            break

    # if none found in the html, try with common urls, provided that they exist
    # and are xml content
    common_paths = ['/feed', '/rss', '/feed.xml', '/rss.xml']
    for path in common_paths:
        rss_url = make_absolute(url, path)
        res = requests.head(rss_url)
        if res.ok and res.headers.get('Content-Type', '').endswith('xml'):
            feed_url = rss_url
            break

    return feed_url, title


def extract_meta(soup, tag):
    meta_tag = soup.find("meta", property=tag, content=True)
    if meta_tag:
        return meta_tag['content']


def make_absolute(url, path):
    "If `path` is a relative url, join it with the given absolute url."
    if not urllib.parse.urlparse(path).netloc:

        path = urllib.parse.urljoin(url, path)
    return path


def pretty_print(url):
    feed = feedparser.parse(url)
    pp = pprint.PrettyPrinter(depth=10)
    pp.pprint(feed)


def to_datetime(struct_time):
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))


def short_date_handler(date_str):
    """
    Handle dates like 'August 14, 2023'.
    """
    return datetime.datetime.strptime(date_str, '%B %d, %Y').timetuple()


feedparser.registerDateHandler(short_date_handler)
