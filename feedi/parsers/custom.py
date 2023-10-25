import datetime
import json

import dateparser
from bs4 import BeautifulSoup
from feedi.requests import CachingRequestsMixin, requests


def fetch(feed_name, url):
    parser = None
    for cls in CustomParser.__subclasses__():
        if cls.is_compatible(url):
            parser = cls(feed_name, url)

    if not parser:
        raise ValueError("no custom parser for %s", url)

    return parser.fetch()


class CustomParser(CachingRequestsMixin):
    BASE_URL = 'TODO override'

    def __init__(self, feed_name, url):
        super().__init__()
        self.feed_name = feed_name
        self.url = url

    @classmethod
    def is_compatible(cls, feed_url):
        return cls.BASE_URL in feed_url

    def fetch(self):
        raise NotImplementedError


class AgendaBAParser(CustomParser):
    BASE_URL = 'https://laagenda.buenosaires.gob.ar'

    def fetch(self):
        api_url = f'{self.BASE_URL}/currentChannel.json'
        response = requests.get(api_url)
        items = response.json()['firstElements'][0]['items']['data']

        entry_values = []
        for item in items:
            created = datetime.datetime.fromisoformat(item['created_at'])
            content_url = f'{self.BASE_URL}?contenido={item["id"]}'
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

        return entry_values


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

        return entry_values


class EternaCadenciaParser(CustomParser):
    BASE_URL = 'https://eternacadencia.com.ar'

    def fetch(self):
        url = f'{self.BASE_URL}/blog'
        response = requests.get(url)
        soup = BeautifulSoup(response.content, 'lxml')

        entry_values = []
        for article in soup.find_all(class_='news'):
            content_url = self.BASE_URL + article.find('a')['href']
            author = article.find(class_='tag')
            if author:
                author = author.text

            date_str = article.find(class_='newsDate').text
            date = dateparser.parse(date_str, languages=['es'])

            entry_values.append({
                'remote_id': content_url.split('/')[-1],
                'title': article.find(class_='newsTitle').text,
                'username': author,
                'remote_created': date,
                'remote_updated': date,
                'body': article.find(class_='newsSummary').text,
                'media_url': article.find('img')['src'],
                'entry_url': content_url,
                'content_url': content_url,
            })

        return entry_values


class PioneerWorksParser(CustomParser):
    BASE_URL = 'https://pioneerworks.org/'

    def fetch(self):
        url = f'{self.BASE_URL}/broadcast/directory'
        response = requests.get(url)
        script = BeautifulSoup(response.content, 'lxml').find(id='__NEXT_DATA__').text
        directory = json.loads(script)['props']['pageProps']['directory']

        entry_values = []
        for article in directory:
            if not article.get('pubDate') or article.get('_type') != 'article':
                continue
            pub_date = datetime.datetime.fromisoformat(article.get('pubDate').split('Z')[0])

            # FIXME we should add support for skip older and min entries instead of this ad hoc check
            if datetime.datetime.now() - pub_date > datetime.timedelta(days=30):
                continue

            article_url = f'{self.BASE_URL}/broadcast/{article["slug"]["current"]}'

            entry_values.append({
                'raw_data': json.dumps(article),
                'remote_id': article['_id'],
                'title': article['title'],
                'username': article['byline'],
                'remote_created': pub_date,
                'remote_updated': pub_date,
                'body': self.fetch_meta(article_url, 'og:description', 'description'),
                'media_url': self.fetch_meta(article_url, 'og:image', 'twitter:image'),
                'content_url': article_url,
            })

        return entry_values
