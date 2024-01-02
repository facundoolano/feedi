import functools

import requests

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36'
TIMEOUT_SECONDS = 5

requests = requests.Session()
requests.headers.update({'User-Agent': USER_AGENT})

# always use a default timeout
requests.get = functools.partial(requests.get, timeout=TIMEOUT_SECONDS)
