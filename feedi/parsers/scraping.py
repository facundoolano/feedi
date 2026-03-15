import logging
import urllib

import favicon.favicon as favicon
from bs4 import BeautifulSoup

from .requests import USER_AGENT, requests

logger = logging.getLogger(__name__)


def get_favicon(url, html=None):
    "Return the best favicon from the given url, or None."
    url_parts = urllib.parse.urlparse(url)
    url = f"{url_parts.scheme}://{url_parts.netloc}"

    try:
        if not html:
            favicons = favicon.get(url, headers={"User-Agent": USER_AGENT}, timeout=2)
        else:
            favicons = sorted(favicon.tags(url, html), key=lambda i: i.width + i.height, reverse=True)
    except Exception:
        logger.exception("error fetching favicon: %s", url)
        return

    # if there's an .ico one, prefer it since it's more likely to be
    # a square icon rather than a banner
    ico_format = [f for f in favicons if f.format == "ico"]
    if ico_format:
        return ico_format[0].url

    return favicons[0].url if favicons else None


class CachingRequestsMixin:
    """
    Exposes a request method that caches the response contents for subsequent requests.
    """

    def __init__(self):
        self.response_cache = {}

    # TODO make this a proper cache of any sort of request, and cache all.
    def request(self, url):
        """
        GET the content of the given url, and if the response is successful
        cache it for subsequent calls to this method.
        """
        if url in self.response_cache:
            logger.debug("using cached response %s", url)
            return self.response_cache[url]

        logger.debug("making request %s", url)
        content = requests.get(url).content
        self.response_cache[url] = content
        return content

    def fetch_meta(self, url, *tags):
        """
        GET the body of the url (which could be already cached) and extract the content of the given meta tag.
        """
        soup = BeautifulSoup(self.request(url), "lxml")
        return extract_meta(soup, *tags)


def extract_meta(soup, *tags):
    for tag in tags:
        for attr in ["property", "name", "itemprop"]:
            meta_tag = soup.find("meta", {attr: tag}, content=True)
            if meta_tag:
                return meta_tag["content"]


def all_meta(soup):
    result = {}
    for attr in ["property", "name", "itemprop"]:
        for meta_tag in soup.find_all("meta", {attr: True}, content=True):
            result[meta_tag[attr]] = meta_tag["content"]
    return result


def make_absolute(url, path):
    "If `path` is a relative url, join it with the given absolute url."
    if not urllib.parse.urlparse(path).netloc:
        path = urllib.parse.urljoin(url, path)
    return path
