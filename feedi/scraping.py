import io
import json
import logging
import subprocess
import urllib
import zipfile

import dateparser

# use internal module to access unexported .tags function
import favicon.favicon as favicon
from bs4 import BeautifulSoup
from PIL import Image

from feedi.requests import USER_AGENT, requests, TIMEOUT_SLOWER
from requests.exceptions import RequestException

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


# TODO this should be renamed, and maybe other things in this modules, using extract too much
def extract(url=None, html=None):
    # The mozilla/readability npm package shows better results at extracting the
    # article content than all the python libraries I've tried... even than the readabilipy
    # one, which is a wrapper of it. so resorting to running a node.js script on a subprocess
    # for parsing the article sadly this adds a dependency to node and a few npm pacakges
    if url:
        html = requests.get(url).content
    elif not html:
        raise ValueError("Expected either url or html")

    r = subprocess.run(["feedi/extract_article.js", "--stdin", url], input=html, capture_output=True, check=True)

    article = json.loads(r.stdout)

    # load lazy images by replacing putting the data-src into src and stripping other attrs
    soup = BeautifulSoup(article["content"], "lxml")

    LAZY_DATA_ATTRS = ["data-src", "data-lazy-src", "data-td-src-property", "data-srcset"]
    for data_attr in LAZY_DATA_ATTRS:
        for img in soup.findAll("img", attrs={data_attr: True}):
            img.attrs = {"src": img[data_attr]}

    # prevent video iframes to force dimensions
    for iframe in soup.findAll("iframe", height=True):
        del iframe["height"]

    article["content"] = str(soup)

    return article


def package_epub(url, article):
    """
    Extract the article content, convert it to a valid html doc, localize its images, write
    everything as a zip and add the proper EPUB metadata. Returns the zipped bytes.
    """

    output_buffer = io.BytesIO()
    with zipfile.ZipFile(output_buffer, "w") as zip:
        # mimetype should be the first file in the container and it should be uncompressed
        # https://www.w3.org/TR/epub-33/#sec-zip-container-mime
        zip.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)

        soup = BeautifulSoup(article["content"], "lxml")
        for img in soup.findAll("img"):
            img_url = img["src"]
            img_filename = "article_files/" + img["src"].split("/")[-1].split("?")[0]
            img_filename = img_filename.replace(".webp", ".jpg")

            # update each img src url to point to the local copy of the file
            img["src"] = img_filename

            # download the image and save into the files subdir of the zip
            try:
                response = requests.get(img_url, timeout=TIMEOUT_SLOWER)
                if not response.ok:
                    continue
            except RequestException:
                logger.exception("error fetching image during epub generation: %s", img_url)
                continue

            with zip.open(img_filename, "w") as dest_file:
                if img_url.endswith(".webp"):
                    # when the image is of a known unsupported format, convert it to jpg first
                    jpg_img = Image.open(io.BytesIO(response.content)).convert("RGB")
                    jpg_img.save(dest_file, "JPEG")
                else:
                    # else write as is
                    dest_file.write(response.content)

        zip.writestr("article.html", str(soup), compress_type=zipfile.ZIP_DEFLATED)

        # epub boilerplate based on https://github.com/thansen0/sample-epub-minimal
        zip.writestr(
            "META-INF/container.xml",
            """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>""",
            compress_type=zipfile.ZIP_DEFLATED,
        )

        author = article["byline"] or article["siteName"]
        if not author:
            # if no explicit author in the website, use the domain
            author = urllib.parse.urlparse(url).netloc.replace("www.", "")

        published = article.get("publishedTime") or ""
        published = published and dateparser.parse(published)
        published = published and published.date().isoformat()

        zip.writestr(
            "content.opf",
            f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" xml:lang="en" unique-identifier="uid" prefix="cc: http://creativecommons.org/ns#">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title id="title">{article['title']}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{article.get('lang', '')}</dc:language>
    <dc:date>{published}</dc:date>
  </metadata>
  <manifest>
    <item id="article" href="article.html" media-type="text/html" />
  </manifest>
  <spine toc="ncx">
   <itemref idref="article" />
  </spine>
</package>""",
            compress_type=zipfile.ZIP_DEFLATED,
        )

    return output_buffer.getvalue()
