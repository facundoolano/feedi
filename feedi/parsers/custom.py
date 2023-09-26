import datetime
import json
import urllib

from bs4 import BeautifulSoup
from feedi.parsers.base import BaseParser
from feedi.requests import requests


def get_best_parser(url):
    # Try with all the customized parsers, and if none is compatible default to the generic RSS parsing.
    for cls in CustomParser.__subclasses__():
        if cls.is_compatible(url):
            return cls
    raise ValueError("no custom parser for %s", url)


class CustomParser(BaseParser):
    BASE_URL = 'TODO override'

    @classmethod
    def is_compatible(cls, feed_url):
        return cls.BASE_URL in feed_url

    def parse_entry_url(self, entry):
        return self.parse_content_url(entry)

    def parse_remote_updated(self, entry):
        return self.parse_remote_created(entry)

    def parse_avatar_url(self, entry):
        return None


class AgendaBAParser(CustomParser):
    BASE_URL = 'https://laagenda.buenosaires.gob.ar'

    @classmethod
    def is_compatible(cls, feed_url):
        return cls.BASE_URL in feed_url

    def fetch(self, _url):
        api_url = f'{self.BASE_URL}/currentChannel.json'
        response = requests.get(api_url)
        items = response.json()['firstElements'][0]['items']['data']
        return None, items

    def parse_remote_id(self, entry):
        return entry['id']

    def parse_title(self, entry):
        return entry['name']

    def parse_username(self, entry):
        return entry['additions'].split(';')[0].split('Por ')[-1]

    def parse_remote_created(self, entry):
        return datetime.datetime.fromisoformat(entry['created_at'])

    def parse_body(self, entry):
        return entry['synopsis']

    def parse_media_url(self, entry):
        return entry['image']['url']

    def parse_content_url(self, entry):
        return f'{self.BASE_URL}?contenido={entry["id"]}'


class RevistaLenguaParser(CustomParser):
    BASE_URL = 'https://www.penguinlibros.com'

    def fetch(self, _url):
        url = f'{self.BASE_URL}/es/revista-lengua/entradas'
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'lxml')

        items = []
        for a in soup.select('.post-title a'):
            article_html = self.request(a['href'])
            article_soup = BeautifulSoup(article_html, 'lxml')
            script = article_soup.find_all('script', type="application/ld+json")[1]
            spec = json.loads(script.text)
            del spec['articleBody']
            items.append(spec)

        return None, items

    def parse_remote_id(self, entry):
        return entry['url']

    def parse_title(self, entry):
        return entry['headline']

    def parse_username(self, entry):
        return entry['editor']

    def parse_remote_created(self, entry):
        return datetime.datetime.fromisoformat(entry['dateCreated'])

    def parse_remote_updated(self, entry):
        return datetime.datetime.fromisoformat(entry['dateModified'])

    def parse_body(self, entry):
        return entry['description']

    def parse_media_url(self, entry):
        return entry['image']

    def parse_entry_url(self, entry):
        return entry['url']

    def parse_content_url(self, entry):
        # this website does very funky things with the html
        # can't really make them work on the reader
        return None
