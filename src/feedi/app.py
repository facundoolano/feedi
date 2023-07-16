import os

import feedparser
from flask import Flask, render_template

app = Flask(__name__)

GOODREADS_TOKEN = os.getenv("GOODREADS_TOKEN")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

HARDCODED_FEEDS = {
    "Apuntes Inchequeables": "https://facundoolano.github.io/feed.xml",
    "@grumpygamer": "https://mastodon.gamedev.place/@grumpygamer.rss",
    "lobset.rs": "https://lobste.rs/rss",
    "Github": f"https://github.com/facundoolano.private.atom?token={GITHUB_TOKEN}",
    "ambito.com": "https://www.ambito.com/rss/pages/home.xml",
    "Goodreads": f"https://www.goodreads.com/user/updates_rss/19714153?key={GOODREADS_TOKEN}"
}


@app.route("/")
def hello_world():

    entries = []
    for feed_name, url in HARDCODED_FEEDS.items():
        feed = feedparser.parse(url)
        for entry in feed['entries']:
            entries.append({'feed': feed_name,
                            'title': entry.get('title', '[no title]'),
                            'url': entry['link'],
                            'body': entry['summary'],
                            'date': entry['published'],
                            'date_parsed': entry['published_parsed']})

    entries.sort(key=lambda e: e['date_parsed'], reverse=True)

    return render_template('base.html', entries=entries)
