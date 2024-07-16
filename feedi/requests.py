import functools

import requests

USER_AGENT = 'feedi/0.1.0 (+https://github.com/facundoolano/feedi)'
TIMEOUT_SECONDS = 5

requests = requests.Session()
requests.headers.update({'User-Agent': USER_AGENT})

# always use a default timeout
requests.get = functools.partial(requests.get, timeout=TIMEOUT_SECONDS)
