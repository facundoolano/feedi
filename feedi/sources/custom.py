import datetime

from bs4 import BeautifulSoup
from feedi.requests import requests
from feedi.sources.base import BaseParser


def get_best_parser(url):
    # Try with all the customized parsers, and if none is compatible default to the generic RSS parsing.
    for cls in CustomParser.__subclasses__():
        if cls.is_compatible(url):
            return cls
    raise ValueError("no custom parser for %s", url)


class CustomParser(BaseParser):
    @staticmethod
    def is_compatible(_feed_url):
        """
        To be overridden by subclasses, this method inspects the url to decide if a given parser
        class is suited to parse the source at the given url.
        """
        raise NotImplementedError


class AgendaBAParser(CustomParser):
    BASE_URL = 'https://laagenda.buenosaires.gob.ar'

    @classmethod
    def is_compatible(cls, feed_url):
        return cls.BASE_URL in feed_url

    def fetch(self, _url, _previous_fetch):
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

    def parse_remote_updated(self, entry):
        return self.parse_remote_created(entry)

    def parse_body(self, entry):
        return entry['synopsis']

    def parse_media_url(self, entry):
        return entry['image']['url']

    def parse_content_url(self, entry):
        return f'{self.BASE_URL}?contenido={entry["id"]}',
