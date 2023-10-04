from feedi.parsers.rss import *


class RedditParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'reddit.com' in feed_url

    def parse_body(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')
        link_url = soup.find("a", string="[link]")
        comments_url = soup.find("a", string="[comments]")

        if link_url['href'] == comments_url['href']:
            # this looks like it's a local reddit discussion
            # return the summary instead of fetching description

            # remove the links from the body first
            link_url.decompose()
            comments_url.decompose()
            return str(soup)

        return self.fetch_meta(link_url, 'og:description', 'description')

    def parse_content_url(self, entry):
        soup = BeautifulSoup(entry['summary'], 'lxml')
        return soup.find("a", string="[link]")['href']

    def parse_entry_url(self, entry):
        # this particular feed puts the reddit comments page in the link
        return entry['link']


class LobstersParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'lobste.rs' in feed_url

    def parse_body(self, entry):
        # skip link-only posts
        if 'Comments' in entry['summary']:
            url = self.parse_content_url(entry)
            return self.fetch_meta(url, 'og:description', 'description')
        return entry['summary']

    def parse_username(self, entry):
        username = super().parse_username(entry)
        return username.split('@')[0]


class HackerNewsParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'news.ycombinator.com' in feed_url or 'hnrss.org' in feed_url

    def parse_body(self, entry):
        # skip link-only posts
        if 'Article URL' in entry['summary']:
            url = self.parse_content_url(entry)
            return self.fetch_meta(url, 'og:description', 'description')
        return entry['summary']


class GithubFeedParser(RSSParser):
    """
    Parser for the personal Github notifications feed.
    """
    @staticmethod
    def is_compatible(feed_url):
        return 'github.com' in feed_url and 'private.atom' in feed_url

    def parse_body(self, entry):
        return entry['title']

    def parse_username(self, entry):
        return entry['authors'][0]['name']

    def parse_title(self, _entry):
        return None

    def parse_avatar_url(self, entry):
        return entry['media_thumbnail'][0]['url']

    def parse_media_url(self, _entry):
        return None

    def parse_entry_url(self, _entry):
        return None

    def parse_content_url(self, _entry):
        # don't open this in the local reader
        return None


class GoodreadsFeedParser(RSSParser):
    """
    Parser for the Goodreads private home rss feed.
    """
    @staticmethod
    def is_compatible(feed_url):
        return 'goodreads.com' in feed_url and '/home/index_rss' in feed_url

    def parse_body(self, entry):
        # some updates come with escaped html entities
        summary = html.unescape(entry['summary'])
        soup = BeautifulSoup(summary, 'lxml')

        # inline images don't look good
        for img in soup('img'):
            img.decompose()

        # some links are relative
        for a in soup('a'):
            a['href'] = urllib.parse.urljoin('https://www.goodreads.com', a['href'])

        return str(soup)

    def parse_title(self, _entry):
        return None

    def parse_media_url(self, _entry):
        return None

    def parse_entry_url(self, entry):
        return entry['link']

    def parse_content_url(self, _entry):
        # don't open this in the local reader
        return None


class RevistaCrisisParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'revistacrisis.com.ar' in feed_url

    @staticmethod
    def should_skip(entry):
        return 'publi' in entry['title'] or entry['title'].lower().startswith('crisis en el aire')

    def parse_body(self, entry):
        return self.fetch_meta(entry['link'], 'og:description', 'description')


class ACMQueueParser(RSSParser):
    @staticmethod
    def is_compatible(feed_url):
        return 'queue.acm.org' in feed_url

    def parse_body(self, entry):
        content = self.request(entry['link'])
        soup = BeautifulSoup(content, 'lxml')
        title = soup.find('h1')
        return str(title.find_next('p'))

    def parse_username(self, entry):
        content = self.request(entry['link'])
        soup = BeautifulSoup(content, 'lxml')
        title = soup.find('h1')
        author = title.find_next('h3')
        if author:
            return author.text.split(',')[0]
