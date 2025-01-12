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

from feedi import scraping
from feedi.requests import USER_AGENT, requests
from feedi.scraping import CachingRequestsMixin

logger = logging.getLogger(__name__)

feedparser.USER_AGENT = USER_AGENT


def fetch(feed_name, url, skip_older_than, min_amount, previous_fetch, etag, modified, filters):
    parser_cls = RSSParser
    for cls in RSSParser.__subclasses__():
        if cls.is_compatible(url):
            parser_cls = cls

    # TODO these arg distribution between constructor and method probably
    # doesn't make sense anymore
    parser = parser_cls(feed_name, url, skip_older_than, min_amount)
    return parser.fetch(previous_fetch, etag, modified, filters)


def fetch_icon(url):
    # prefer link inside rss as the base url
    feed = feedparser.parse(url)
    feed_link = feed["feed"].get("link", url)
    icon_url = scraping.get_favicon(feed_link)
    if icon_url:
        logger.debug("using feed icon: %s", icon_url)
        return icon_url

    # otherwise try to get the icon from an explicit icon link
    icon_url = feed["feed"].get("icon", feed["feed"].get("webfeeds_icon"))
    if icon_url and requests.get(icon_url).ok:
        logger.debug("using feed icon: %s", icon_url)
        return icon_url

    logger.debug("no feed icon found for %s", url)


class RSSParser(CachingRequestsMixin):
    """
    A generic parser for RSS articles.
    Implements reasonable defaults to parse each entry field, which can be overridden by subclasses
    for custom feed presentation.
    """

    FIELDS = [
        "title",
        "avatar_url",
        "username",
        "content_short",
        "content_full",
        "media_url",
        "remote_id",
        "display_date",
        "sort_date",
        "comments_url",
        "target_url",
        "content_url",
        "header",
    ]

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

        if feed.bozo:
            logger.warning("Failure parsing feed %s %s", self.feed_name, feed.bozo_exception)
            # this doesn't necessarily mean the feed was not parsed, so moving on

        if not feed["feed"]:
            logger.info("skipping empty feed %s %s", self.url, feed.get("debug_message"))
            return None, [], None, None

        etag = getattr(feed, "etag", None)
        modified = getattr(feed, "modified", None)

        entries = []
        for item in feed["items"]:
            try:
                entry = self.parse(item, len(entries), previous_fetch, filters)
                if entry:
                    entry["raw_data"] = json.dumps(item)
                    entries.append(entry)
            except Exception as error:
                exc_desc_lines = traceback.format_exception_only(type(error), error)
                exc_desc = "".join(exc_desc_lines).rstrip()
                logger.error("skipping errored entry %s %s %s", self.feed_name, item.get("link"), exc_desc)
                logger.debug(traceback.format_exc())

        return feed["feed"], entries, etag, modified

    def parse(self, item, parsed_count, previous_fetch, filters):
        """
        Pass the given raw entry data to each of the field parsers to produce an
        entry values dict.
        """
        if self.should_skip(item):
            return

        # or that's too old
        is_first_load = previous_fetch is None
        published = item.get("published_parsed", item.get("updated_parsed"))
        if self.skip_older_than and published and to_datetime(published) < self.skip_older_than:
            # unless it's the first time we're loading it, in which case we prefer to show old stuff
            # to showing nothing
            if not is_first_load or not self.min_amount or parsed_count >= self.min_amount:
                logger.debug("skipping old entry %s", item.get("link"))
                return

        if filters and not self._matches(item, filters):
            logger.debug("skipping entry not matching filters %s %s", item.get("link"), filters)
            return

        result = {}
        for field in self.FIELDS:
            method = "parse_" + field
            result[field] = getattr(self, method)(item)

        return result

    @staticmethod
    def should_skip(_entry):
        # hook for subclasses to apply ad hoc skipping logic
        return False

    @staticmethod
    def _matches(entry, filters):
        """
        Check a filter expression (e.g. "author=John Doe") against the parsed entry and return whether
        it matches the condition.
        """
        # this is very brittle and ad hoc but gets the job done
        filters = filters.split(",")
        for filter in filters:
            field, value = filter.strip().split("=")
            field = field.lower().strip()
            value = value.lower().strip()

            if value not in entry.get(field, "").lower():
                return False

        return True

    def parse_title(self, entry):
        return entry.get("title") or self.fetch_meta(self.parse_content_url(entry), "og:title")

    def parse_content_url(self, entry):
        return entry["link"]

    def parse_target_url(self, entry):
        # assume that whatever is identified as content url is the safe default for target
        return self.parse_content_url(entry)

    def parse_comments_url(self, entry):
        return entry.get("comments")

    def parse_username(self, entry):
        # TODO if missing try to get from meta?
        author = entry.get("author", "")
        if author:
            author = BeautifulSoup(author, "lxml").text

        author = author.split(",")[0]

        if "(" in author:
            author = author.split("(")[1].split(")")[0]

        return author

    def parse_avatar_url(self, entry):
        url = entry.get("source", {}).get("icon")
        if url and requests.get(url).ok:
            logger.debug("found entry-level avatar %s", url)
            return url

    def parse_content_short(self, entry):
        content_url = self.parse_content_url(entry)
        summary = entry.get("summary")
        if summary:
            # wordpress adds an annoying footer by default ('the post x appeared first on')
            # removing it by skipping the last line when it includes a link to the article
            footer = summary.split("\n")[-1]
            if content_url.split("?")[0] in footer:
                summary = summary.replace(footer, "").strip()

            summary = html.unescape(summary)
        else:
            if not content_url:
                return
            summary = self.fetch_meta(content_url, "og:description", "description")
            if not summary:
                return

        soup = BeautifulSoup(summary, "lxml")

        # remove images in case there are any inside a paragraph
        for tag in soup("img"):
            tag.decompose()
        # return the rest of the html untouched, assuming any truncating will be done
        # on the view side if necessary (so it applies regardless of the parser implementation)
        return str(soup)

    def parse_content_full(self, _entry):
        # by default skip the full content parsing since it's too expensive to do on every article
        return None

    def parse_media_url(self, entry):
        # first try to get it in standard feed fields
        if "media_thumbnail" in entry:
            return entry["media_thumbnail"][0]["url"]

        if "media_content" in entry and entry["media_content"][0].get("type") == "image":
            return entry["media_content"][0]["url"]

        # else try to extract it from the summary html
        if "summary" in entry:
            soup = BeautifulSoup(entry["summary"], "lxml")
            if soup.img:
                return soup.img["src"]

        parsed_dest_url = self.parse_content_url(entry)
        return self.fetch_meta(parsed_dest_url, "og:image", "twitter:image")

    def parse_remote_id(self, entry):
        return entry.get("id", entry["link"])

    def parse_display_date(self, entry):
        dt = to_datetime(entry.get("published_parsed", entry.get("updated_parsed")))
        if dt > datetime.datetime.utcnow():
            raise ValueError(f"publication date is in the future {dt}")
        return dt

    def parse_sort_date(self, entry):
        dt = to_datetime(entry["updated_parsed"])
        if dt > datetime.datetime.utcnow():
            raise ValueError("publication date is in the future")
        return dt

    def parse_header(self, entry):
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

    # assume the url is already a feed url
    parsed = feedparser.parse(res.content)
    if not parsed.bozo:
        # no error, looks like a proper feed
        title = parsed.feed.get("title")
        return url, title

    soup = BeautifulSoup(res.content, "lxml")

    # resolve title
    title = scraping.extract_meta(soup, "og:site_name", "og:title")
    if not title:
        title = soup.find("title")
        if title:
            title = title.text

    link_types = ["application/rss+xml", "application/atom+xml", "application/x.atom+xml", "application/x-atom+xml"]

    feed_url = None
    # first try with the common link tags for feeds
    for type in link_types:
        link = soup.find(["link", "a"], type=type, href=True)
        if link:
            feed_url = scraping.make_absolute(url, link["href"])
            return feed_url, title

    # if none found in the html, try with common urls, provided that they exist
    # and are xml content
    common_paths = ["/feed", "/rss", "/feed.xml", "/rss.xml"]
    for path in common_paths:
        rss_url = scraping.make_absolute(url, path)
        res = requests.get(rss_url)
        mime = res.headers.get("Content-Type", "").split(";")[0]
        if res.ok and mime.endswith("xml"):
            return rss_url, title

    return None, title


def pretty_print(url):
    feed = feedparser.parse(url)
    pp = pprint.PrettyPrinter(depth=10)
    pp.pprint(feed)


def to_datetime(struct_time):
    try:
        return datetime.datetime.fromtimestamp(time.mktime(struct_time))
    except Exception:
        logger.error("Failure in date parsing, received %s", struct_time)
        raise


def short_date_handler(date_str):
    """
    Handle dates like 'August 14, 2023'.
    """
    return datetime.datetime.strptime(date_str, "%B %d, %Y").timetuple()


feedparser.registerDateHandler(short_date_handler)


class RedditInboxParser(RSSParser):
    "Parser for message inboxes, see https://www.reddit.com/prefs/feeds/ when logged in."

    @staticmethod
    def is_compatible(feed_url):
        return "reddit.com/message" in feed_url

    def parse_content_short(self, entry):
        return entry["content"][0]["value"]

    def parse_title(self, entry):
        return entry["title"].split(": ")[-1].capitalize()


class RedditParser(RSSParser):
    "Parser for public or private reddit listings (i.e. subreddits, user messages, home feed, etc.)"

    @staticmethod
    def is_compatible(feed_url):
        # looks like reddit but not like the inbox feed
        return "reddit.com" in feed_url and "reddit.com/message" not in feed_url

    def parse_content_short(self, entry):
        soup = BeautifulSoup(entry["summary"], "lxml")
        link_anchor = soup.find("a", string="[link]")
        comments_anchor = soup.find("a", string="[comments]")

        if link_anchor["href"] == comments_anchor["href"]:
            # this looks like it's a local reddit discussion
            # return the summary instead of fetching description

            # remove the links from the body first
            link_anchor.decompose()
            comments_anchor.decompose()
            return str(soup)

        return self.fetch_meta(link_anchor["href"], "og:description", "description")

    def parse_content_url(self, entry):
        target = self.parse_target_url(entry)
        # use old.reddit for content fetching, which I think is less likely to be blocked?
        return target.replace("www.", "").replace("https://reddit.com", "https://old.reddit.com")

    def parse_target_url(self, entry):
        soup = BeautifulSoup(entry["summary"], "lxml")
        return soup.find("a", string="[link]")["href"]

    def parse_comments_url(self, entry):
        # this particular feed puts the reddit comments page in the link
        return entry["link"]

    def parse_username(self, entry):
        # instead of showing the username show the subreddit name when available
        # this is kind of an abuse but yields a more useful UI
        if entry.get("tags", []):
            return entry["tags"][0]["label"]

        return super().parse_username(entry)


class LobstersParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return "lobste.rs" in feed_url

    def parse_content_short(self, entry):
        # fill summary from source for link-only posts
        if "Comments" in entry["summary"]:
            url = self.parse_content_url(entry)
            return self.fetch_meta(url, "og:description", "description")
        return entry["summary"]

    def parse_username(self, entry):
        username = super().parse_username(entry)
        return username.split("@")[0]


class HackerNewsParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return "news.ycombinator.com" in feed_url or "hnrss.org" in feed_url

    def parse_content_short(self, entry):
        # fill summary from source for link-only posts
        if "Article URL" in entry["summary"]:
            url = self.parse_content_url(entry)
            return self.fetch_meta(url, "og:description", "description")
        return entry["summary"]


class GithubFeedParser(RSSParser):
    """
    Parser for the personal Github notifications feed.
    """

    @staticmethod
    def is_compatible(feed_url):
        return "github.com" in feed_url and "private.atom" in feed_url

    def parse_content_short(self, entry):
        return entry["title"]

    def parse_username(self, entry):
        return entry["authors"][0]["name"]

    def parse_title(self, _entry):
        return None

    def parse_avatar_url(self, entry):
        return entry["media_thumbnail"][0]["url"]

    def parse_media_url(self, _entry):
        return None

    def parse_content_url(self, _entry):
        # don't open this in the local reader
        return None

    def parse_target_url(self, _entry):
        # don't open github
        return None


class GoodreadsFeedParser(RSSParser):
    """
    Parser for the Goodreads private home rss feed.
    """

    @staticmethod
    def is_compatible(feed_url):
        return "goodreads.com" in feed_url and "/home/index_rss" in feed_url

    def parse_content_short(self, entry):
        # some updates come with escaped html entities
        summary = html.unescape(entry["summary"])
        soup = BeautifulSoup(summary, "lxml")

        # inline images don't look good
        for img in soup("img"):
            img.decompose()

        # some links are relative
        for a in soup("a"):
            a["href"] = urllib.parse.urljoin("https://www.goodreads.com", a["href"])

        return str(soup)

    def parse_title(self, _entry):
        return None

    def parse_media_url(self, _entry):
        return None

    def parse_target_url(self, entry):
        return entry["link"]

    def parse_content_url(self, _entry):
        # don't open this in the local reader
        return None


class RevistaCrisisParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return "revistacrisis.com.ar" in feed_url

    @staticmethod
    def should_skip(entry):
        return "publi" in entry["title"] or entry["title"].lower().startswith("crisis en el aire")

    def parse_content_short(self, entry):
        return self.fetch_meta(entry["link"], "og:description", "description")


class ACMQueueParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return "queue.acm.org" in feed_url

    def parse_content_short(self, entry):
        content = self.request(entry["link"])
        soup = BeautifulSoup(content, "lxml")
        title = soup.find("h1")
        return str(title.find_next("p"))

    def parse_username(self, entry):
        content = self.request(entry["link"])
        soup = BeautifulSoup(content, "lxml")
        title = soup.find("h1")
        author = title.find_next("h3")
        if author:
            return author.text.split(",")[0]


class WikiFeaturedParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return "wikipedia.org" in feed_url and "featuredfeed" in feed_url

    def parse_content_short(self, entry):
        soup = BeautifulSoup(entry["summary"], "lxml")
        return str(soup.find("p"))

    def parse_title(self, entry):
        soup = BeautifulSoup(entry["summary"], "lxml")
        return soup.find("p").find("a").text


class IndieBlogParser(RSSParser):
    @staticmethod
    def is_compatible(_feed_url):
        return "indieblog.page" in _feed_url

    def parse_content_short(self, entry):
        soup = BeautifulSoup(entry["summary"], "lxml")
        body = soup.blockquote
        body.name = "p"
        return str(body)
