import logging
import urllib

import favicon
from bs4 import BeautifulSoup

import requests

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'

requests = requests.Session()
requests.headers.update({'User-Agent': USER_AGENT})

logger = logging.getLogger(__name__)


# TODO review if this module is a good place for this kind of utilities
def get_favicon(url):
    "Return the best favicon from the given url, or None."
    url_parts = urllib.parse.urlparse(url)
    url = f'{url_parts.scheme}://{url_parts.netloc}'

    try:
        favicons = favicon.get(url)
    except:
        logger.exception("error fetching favicon: %s", url)
        return

    # return the first of the results that is a square image
    clean_favicons = [f for f in favicons if f.height == f.width and
                      requests.get(f.url).ok]  # I'd prefer head but some urls return 405 for it
    if not clean_favicons:
        logger.debug("no feed icon found: %s", favicons)
        return
    icon_url = clean_favicons[0].url
    logger.debug('using favicon %s', icon_url)

    return icon_url


def get_image_dimensions(url):
    try:
        res = requests.get(url, stream=True)
    except:
        logger.exception("error loading image from %s", url)
        return

    if res.ok:
        res.raw.decode_content = True
        try:
            img = Image.open(res.raw)
            return img.width, img.height
        except:
            logger.exception("error loading image from %s", url)
    else:
        logger.warn("error fetching image %s", url)


class CachingRequestsMixin:
    """
    Exposes a request method that caches the response contents for subsequent requests.
    """

    def __init__(self):
        self.response_cache = {}

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

    def fetch_meta(self, url, *tags):
        """
        GET the body of the url (which could be already cached) and extract the content of the given meta tag.
        """
        soup = BeautifulSoup(self.request(url), 'lxml')
        return extract_meta(soup, *tags)


def extract_meta(soup, *tags):
    for tag in tags:
        for attr in ['property', 'name', 'itemprop']:
            meta_tag = soup.find("meta", {attr: tag}, content=True)
            if meta_tag:
                return meta_tag['content']
