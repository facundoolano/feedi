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
