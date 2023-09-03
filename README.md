# feedi

TODO

## Local setup

Requires Python 3 and nodejs (for the reader functionality).
TODO document python and OS lib requirements
(TODO what versions?)

Install:

    make venv deps

Run the development server:

    make dev

Run a production like server:

    make prod


## Advanced usage
### Bulk load feeds from csv

`make feed-load` will load feeds from a local `feeds.csv` file.

Example file (the $VARS are for illustration, they should be replaced before running the command):


    rss,"Apuntes Inchequeables","https://facundoolano.github.io/feed.xml"
    rss,"lobste.rs","https://lobste.rs/rss"
    rss,"hackernews","https://hnrss.org/newest?points=100"
    rss,"Github","https://github.com/$USERNAME.private.atom?token=$TOKEN"
    rss,"Goodreads","https://www.goodreads.com/home/index_rss/$ID?key=$TOKEN
    mastodon,$NAME,$SERVER,$ACCES_TOKEN


### Mastodon account setup

TODO

see https://mastodonpy.readthedocs.io/en/stable/#usage

### Custom feed parsing

TODO


### Kindle device setup

The app allows to register a kindle device (statically in the configuration, for now) to send the cleaned up article HTML to it. This uses the [stkclient](https://github.com/maxdjohnson/stkclient) library.

To setup the device in the config:

``` python
import stkclient

a = stkclient.OAuth2()
signin_url = a.get_signin_url()
# Open `signin_url` in a browser, sign in and authorize the application, pass
# the final redirect_url below
client = a.create_client(redirect_url)

with open('kindle.creds', 'w') as fp:
    client.dump(fp)
```

Then update [the config](https://github.com/facundoolano/feedi/blob/a7a0c6e8b13b790cc80b499bb9a9d9a55e8f975b/feedi/config.py#L13-L16) to point to the credentials file:

    KINDLE_CREDENTIALS_PATH = 'kindle.creds'


### Keyboard shortcuts

TODO

## Design notes

TODO
