import datetime
import json

from bs4 import BeautifulSoup
from feedi.parsers.base import BaseParser
from feedi.requests import requests


def get_best_parser(url):
    for cls in CustomParser.__subclasses__():
        if cls.is_compatible(url):
            return cls
    raise ValueError("no custom parser for %s", url)


class CustomParser(BaseParser):
    BASE_URL = 'TODO override'

    @classmethod
    def is_compatible(cls, feed_url):
        return cls.BASE_URL in feed_url


class AgendaBAParser(CustomParser):
    BASE_URL = 'https://laagenda.buenosaires.gob.ar'

    def fetch(self):
        api_url = f'{self.BASE_URL}/currentChannel.json'
        response = requests.get(api_url)
        items = response.json()['firstElements'][0]['items']['data']

        entry_values = []
        for item in items:
            created = datetime.datetime.fromisoformat(item['created_at'])
            content_url = '{self.BASE_URL}?contenido={entry["id"]}'
            entry_values.append({
                'remote_id': item['id'],
                'title': item['name'],
                'username': item['additions'].split(';')[0].split('Por ')[-1],
                'remote_created': created,
                'remote_updated': created,
                'body': item['synopsis'],
                'media_url': item['image']['url'],
                'content_url': content_url,
                'entry_url': content_url,
                'raw_data': json.dumps(item)
            })

        return None, entry_values


class RevistaLenguaParser(CustomParser):
    BASE_URL = 'https://www.penguinlibros.com'

    def fetch(self):
        url = f'{self.BASE_URL}/es/revista-lengua/entradas'
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'lxml')

        entry_values = []
        for a in soup.select('.post-title a'):
            article_html = self.request(a['href'])
            article_soup = BeautifulSoup(article_html, 'lxml')
            script = article_soup.find_all('script', type="application/ld+json")[1]
            item = json.loads(script.text)
            del item['articleBody']

            entry_values.append({
                'raw_data': json.dumps(item),
                'remote_id': item['url'],
                'title': item['headline'],
                'username': item['editor'],
                'remote_created': datetime.datetime.fromisoformat(item['dateCreated']),
                'remote_updated': datetime.datetime.fromisoformat(item['dateModified']),
                'body': item['description'],
                'media_url': item['image'],
                'entry_url': item['url'],
                # this website does very funky things with the html
                # can't really make them work on the reader
                'content_url': None,
            })

        return None, entry_values
