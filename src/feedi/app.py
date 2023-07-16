import os

import feedparser
from flask import Flask

app = Flask(__name__)

GOODREADS_TOKEN = os.getenv("GOODREADS_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

HARDCODED_FEEDS = [
    "https://facundoolano.github.io/feed.xml",
    "https://mastodon.gamedev.place/@grumpygamer.rss",
    "https://lobste.rs/rss",
    f"https://github.com/facundoolano.private.atom?token={GITHUB_TOKEN}",
    "https://www.ambito.com/rss/pages/home.xml",
    f"https://www.goodreads.com/user/updates_rss/19714153?key={GOODREADS_TOKEN}"
]


@app.route("/")
def hello_world():

    all_entries = []
    for source in HARDCODED_FEEDS:
        feed = feedparser.parse(source)
        for entry in feed['entries']:
            all_entries.append({'title': entry.get('title', '[no title]'),
                                'url': entry['link'],
                                'body': entry['summary'],
                                'date': entry['published_parsed']})

    all_entries.sort(key=lambda e: e['date'], reverse=True)

    return "<p>Hello, World!</p>"
