import logging
import urllib

import favicon

import requests

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'

requests = requests.Session()
requests.headers.update({'User-Agent': USER_AGENT})

logger = logging.getLogger(__name__)


# TODO review if this module is a good place for this kind of utilities
def get_favicon(url):
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
