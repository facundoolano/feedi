import datetime
import io
import json
import logging
import urllib
import zipfile

import dateparser
from bs4 import BeautifulSoup
from flask import current_app as app
from PIL import Image
from requests.exceptions import RequestException

import feedi.email as feedi_email
import feedi.models as models
from feedi.models import db
from feedi.parsers.requests import TIMEOUT_SLOWER, requests
from feedi.parsers.scraping import all_meta, get_favicon

logger = logging.getLogger(__name__)


def fetch_page(user_id, page_arg, hide_seen, is_mixed, **filters):
    """
    Fetch a page of entries from db, optionally applying query filters.
    Returns (entry_page, next_page).

    When pages other than the first are requested, the previous page of entries
    is marked as 'viewed'.
    """
    # already viewed entries should be skipped according to setting
    # but only for views that mix multiple feeds (e.g. home page, folders).
    # If a specific feed is being browsed, it makes sense to show all the entries.
    filters["hide_seen"] = is_mixed and hide_seen

    # pagination includes a start_at timestamp so the entry set remains the same
    # even if new entries are added between requests
    if page_arg:
        start_at, page_num = page_arg.split(":")
        page_num = int(page_num)
        start_at = datetime.datetime.fromtimestamp(float(start_at))
    else:
        start_at = datetime.datetime.utcnow()
        page_num = 1

    if is_mixed:
        filters["newer_than"] = datetime.datetime.utcnow() - datetime.timedelta(days=14)

    query = models.Entry.filter_by(user_id, start_at, **filters)
    entry_page = db.paginate(query, per_page=app.config["ENTRY_PAGE_SIZE"], page=page_num)
    next_page = f"{start_at.timestamp()}:{page_num + 1}" if entry_page.has_next else None

    if entry_page.has_prev:
        # mark the previous page as viewed. The rationale is that the user fetches
        # nth page we can assume the previous one can be marked as viewed.
        previous_ids = [e.id for e in entry_page.prev().items]
        update = (
            db.update(models.Entry).where(models.Entry.id.in_(previous_ids)).values(viewed=datetime.datetime.utcnow())
        )
        db.session.execute(update)
        db.session.commit()

    return entry_page, next_page


def get_from_url(user_id, url):
    """Load an entry for the given article URL if it exists, otherwise fetch its metadata and create one."""
    entry = db.session.scalar(db.select(models.Entry).filter_by(content_url=url, user_id=user_id))

    if not entry:
        response = requests.get(url)
        response.raise_for_status()

        if not response.ok:
            raise Exception()

        soup = BeautifulSoup(response.content, "lxml")
        metadata = all_meta(soup)

        title = metadata.get("og:title", metadata.get("twitter:title", getattr(soup.title, "text")))
        if not title:
            raise ValueError(f"{url} is missing article metadata")

        if "og:article:published_time" in metadata:
            display_date = dateparser.parse(metadata["og:article:published_time"])
        else:
            display_date = datetime.datetime.utcnow()

        values = {
            "remote_id": url,
            "title": title,
            "username": metadata.get("author", "").split(",")[0],
            "display_date": display_date,
            "sort_date": datetime.datetime.utcnow(),
            "content_short": metadata.get("og:description", metadata.get("description")),
            "media_url": metadata.get("og:image", metadata.get("twitter:image")),
            "target_url": url,
            "content_url": url,
            "raw_data": json.dumps(metadata),
            "icon_url": get_favicon(url, html=response.content),
        }
        entry = models.Entry(user_id=user_id, **values)

    return entry


def send_to_kindle(user, url, article):
    """Package article as epub and send to Kindle. Records the entry."""
    attach_data = _package_epub(url, article)
    feedi_email.send(user.kindle_email, attach_data, filename=article["title"])

    entry = get_from_url(user.id, url)
    entry.sent_to_kindle = datetime.datetime.now()
    entry.viewed = entry.viewed or datetime.datetime.utcnow()
    entry.content_full = article["content"]
    db.session.add(entry)
    db.session.commit()


def _package_epub(url, article):
    """
    Convert the article to a valid html doc, localize its images, write
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
    <dc:title id="title">{article["title"]}</dc:title>
    <dc:creator>{author}</dc:creator>
    <dc:language>{article.get("lang", "")}</dc:language>
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
