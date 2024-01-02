import json
import logging
import shutil
import subprocess
import urllib
import zipfile

import favicon
from bs4 import BeautifulSoup

from feedi.requests import requests

logger = logging.getLogger(__name__)


def get_favicon(url):
    "Return the best favicon from the given url, or None."
    url_parts = urllib.parse.urlparse(url)
    url = f'{url_parts.scheme}://{url_parts.netloc}'

    try:
        favicons = favicon.get(url)
    except Exception:
        logger.exception("error fetching favicon: %s", url)
        return

    # if there's an .ico one, prefer it since it's more likely to be
    # a square icon rather than a banner
    ico_format = [f for f in favicons if f.format == 'ico']
    if ico_format:
        return ico_format[0].url

    # otherwise return the first
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


def extract(url=None, html=None):
    # The mozilla/readability npm package shows better results at extracting the
    # article content than all the python libraries I've tried... even than the readabilipy
    # one, which is a wrapper of it. so resorting to running a node.js script on a subprocess
    # for parsing the article sadly this adds a dependency to node and a few npm pacakges
    if html:
        r = subprocess.run(["feedi/extract_article.js"], input=html,
                           capture_output=True, check=True)
    elif url:
        r = subprocess.run(["feedi/extract_article.js", url],
                           capture_output=True, text=True, check=True)
    else:
        raise ValueError('Expected either url or html')

    article = json.loads(r.stdout)

    # load lazy images by replacing putting the data-src into src and stripping other attrs
    soup = BeautifulSoup(article['content'], 'lxml')

    LAZY_DATA_ATTRS = ['data-src', 'data-lazy-src', 'data-td-src-property', 'data-srcset']
    for data_attr in LAZY_DATA_ATTRS:
        for img in soup.findAll('img', attrs={data_attr: True}):
            img.attrs = {'src': img[data_attr]}

    # prevent video iframes to force dimensions
    for iframe in soup.findAll('iframe', height=True):
        del iframe['height']

    article['content'] = str(soup)

    return article


def compress(outfilename, article):
    """
    Extract the article content, convert it to a valid html doc, localize its images and write
    everything as a zip in the given file (which should be open for writing).
    """

    # pass it through bs4 so it's a well-formed html (otherwise kindle will reject it)
    soup = BeautifulSoup(article['content'], 'lxml')

    with zipfile.ZipFile(outfilename, 'w', compression=zipfile.ZIP_DEFLATED) as zip:
        for img in soup.findAll('img'):
            img_url = img['src']
            img_filename = 'article_files/' + img['src'].split('/')[-1].split('?')[0]

            # update each img src url to point to the local copy of the file
            img['src'] = img_filename

            # TODO webp images aren't supported, convert to png or jpg

            # download the image into the zip, inside the files subdir
            with requests.get(img_url, stream=True) as img_src, zip.open(img_filename, mode='w') as img_dest:
                shutil.copyfileobj(img_src.raw, img_dest)

        zip.writestr('article.html', str(soup))
