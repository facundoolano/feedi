import datetime
import html
import json
import logging
import pprint
import time
import traceback
import urllib

import feedparser
from bs4 import BeautifulSoup
from feedi.requests import USER_AGENT, CachingRequestsMixin, requests

logger = logging.getLogger(__name__)

feedparser.USER_AGENT = USER_AGENT


def fetch(feed_name, url, skip_older_than, min_amount,
          previous_fetch, etag, modified, filters):
    parser_cls = RSSParser
    for cls in RSSParser.__subclasses__():
        if cls.is_compatible(url):
            parser_cls = cls

    # TODO these arg distribution between constructor and method probably
    # doesn't make sense anymore
    parser = parser_cls(feed_name, url, skip_older_than, min_amount)
    return parser.fetch(previous_fetch, etag, modified, filters)


def fetch_icon(url):
    # try to get the icon from an rss field
    feed = feedparser.parse(url)
    icon_url = feed['feed'].get('icon', feed['feed'].get('webfeeds_icon'))
    if icon_url and requests.head(icon_url).ok:
        logger.debug("using feed icon: %s", icon_url)
        return icon_url


class RSSParser(CachingRequestsMixin):
    """
    A generic parser for RSS articles.
    Implements reasonable defaults to parse each entry field, which can be overridden by subclasses
    for custom feed presentation.
    """

    FIELDS = ['title', 'avatar_url', 'username', 'body', 'media_url', 'remote_id',
              'remote_created', 'remote_updated', 'entry_url', 'content_url', 'header']

    @staticmethod
    def is_compatible(_feed_url):
        """
        To be overridden by subclasses, this method inspects the url to decide if a given parser
        class is suited to parse the source at the given url.
        """
        raise NotImplementedError

    def __init__(self, feed_name, url, skip_older_than, min_amount):
        super().__init__()
        self.feed_name = feed_name
        self.url = url
        self.skip_older_than = skip_older_than
        self.min_amount = min_amount

    def fetch(self, previous_fetch, etag, modified, filters=None):
        """
        Requests the RSS/Atom feed and, if it has changed, parses recent entries which
        are returned as a list of value dicts.
        """
        # using standard feed headers to prevent re-fetching unchanged feeds
        # https://feedparser.readthedocs.io/en/latest/http-etag.html
        feed = feedparser.parse(self.url, etag=etag, modified=modified)

        if not feed['feed']:
            logger.info('skipping empty feed %s %s', self.url, feed.get('debug_message'))
            return None, [], None, None

        # also checking with the internal updated field in case feed doesn't support the standard headers
        if previous_fetch and 'updated_parsed' in feed and to_datetime(feed['updated_parsed']) <= previous_fetch:
            logger.info('skipping up to date feed %s', self.url)
            return None, [], None, None

        etag = getattr(feed, 'etag', None)
        modified = getattr(feed, 'modified', None)

        entries = []
        is_first_load = previous_fetch is None
        for item in feed['items']:

            # don't try to process stuff that hasn't changed recently
            updated = item.get('updated_parsed', item.get('published_parsed'))
            if updated and previous_fetch and to_datetime(updated) < previous_fetch:
                logger.debug('skipping up to date entry %s', item.get('link'))
                continue

            # or that's too old
            published = item.get('published_parsed', item.get('updated_parsed'))
            if (self.skip_older_than and published and to_datetime(published) < self.skip_older_than):
                # unless it's the first time we're loading it, in which case we prefer to show old stuff
                # to showing nothing
                if not is_first_load or not self.min_amount or len(entries) >= self.min_amount:
                    logger.debug('skipping old entry %s', item.get('link'))
                    continue

            if filters and not self._matches(item, filters):
                logger.debug('skipping entry not matching filters %s %s', item.get('link'), filters)
                continue

            entry = self.parse(item)
            if entry:
                entry['raw_data'] = json.dumps(item)
                entries.append(entry)

        return feed['feed'], entries, etag, modified

    @staticmethod
    def _matches(entry, filters):
        """
        Check a filter expression (e.g. "author=John Doe") against the parsed entry and return whether it matches the condition.
        """
        # this is very brittle and ad hoc but gets the job done
        filters = filters.split(',')
        for filter in filters:
            field, value = filter.strip().split('=')
            field = field.lower().strip()
            value = value.lower().strip()

            if not value in entry.get(field, '').lower():
                return False

        return True

    def parse(self, entry):
        """
        Pass the given raw entry data to each of the field parsers to produce an
        entry values dict.
        """
        result = {}

        for field in self.FIELDS:
            method = 'parse_' + field
            try:
                result[field] = getattr(self, method)(entry)
            except Exception as error:
                exc_desc_lines = traceback.format_exception_only(type(error), error)
                exc_desc = ''.join(exc_desc_lines).rstrip()
                logger.error("skipping errored entry %s %s %s",
                             self.feed_name,
                             entry.get('link'),
                             exc_desc)
                logger.debug(traceback.format_exc())
                return

        return result

    def parse_title(self, entry):
        return entry['title']

    def parse_content_url(self, entry):
        return entry['link']

    def parse_entry_url(self, entry):
        return entry.get('comments') or self.parse_content_url(entry)

    def parse_username(self, entry):
        # TODO if missing try to get from meta?
        author = entry.get('author', '')

        if ',' in author:
            author = author.split(',')[0]

        if '(' in author:
            author = author.split('(')[1].split(')')[0]

        return author

    def parse_avatar_url(self, entry):
        url = entry.get('source', {}).get('icon')
        if url and requests.head(url).ok:
            logger.debug('found entry-level avatar %s', url)
            return url

    def parse_body(self, entry):
        summary = entry.get('summary')
        if not summary:
            url = self.parse_content_url(entry)
            if not url:
                return
            summary = self.fetch_meta(url, 'og:description', 'description')

        soup = BeautifulSoup(summary, 'lxml')

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
        return self.fetch_meta(parsed_dest_url, "og:image", "twitter:image")

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

    def parse_header(self, entry):
        return None


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

        return self.fetch_meta(link_url, 'og:description', 'description')

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
            return self.fetch_meta(url, 'og:description', 'description')
        return entry['summary']

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
            return self.fetch_meta(url, 'og:description', 'description')
        return entry['summary']


class GithubFeedParser(RSSParser):
    """
    Parser for the personal Github notifications feed.
    """
    @staticmethod
    def is_compatible(feed_url):
        return 'github.com' in feed_url and 'private.atom' in feed_url

    def parse_body(self, entry):
        return entry['title']

    def parse_username(self, entry):
        return entry['authors'][0]['name']

    def parse_title(self, _entry):
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

    def parse_body(self, entry):
        # some updates come with escaped html entities
        summary = html.unescape(entry['summary'])
        soup = BeautifulSoup(summary, 'lxml')

        # inline images don't look good
        for img in soup('img'):
            img.decompose()

        # some links are relative
        for a in soup('a'):
            a['href'] = urllib.parse.urljoin('https://www.goodreads.com', a['href'])

        return str(soup)

    def parse_title(self, _entry):
        return None

    def parse_media_url(self, _entry):
        return None

    def parse_entry_url(self, entry):
        return entry['link']

    def parse_content_url(self, _entry):
        # don't open this in the local reader
        return None


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
