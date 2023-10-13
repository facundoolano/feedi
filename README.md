# feedi

feedi is a personal web RSS reader that also works as a (read-only) Mastodon client.

![](feedi.png)

The project is currently experimental and is missing some pieces (and it will likely remain that way for a while),
but feel free to try and hack on it. More details on the [design notes](#design-and-implementation-notes) below.

## Local setup

Requires Python 3 (tested with 3.9 and 3.11) and nodejs (for the reader functionality; tested with node 20).

To install on a local virtual env run:

    make deps-dev

Then a development server can be run at http://localhost:5000 with:

    make dev

A production-like server can also be run at http://localhost:5000 with:

    make prod


## Advanced usage
### Bulk import/export feeds from csv and OPML files

`make feed-load` will load feeds from a local `feeds.csv` file. A [sample file](https://github.com/facundoolano/feedi/blob/HEAD/feeds.csv) is included in the repo
in case you want to see some content right away.

There's also a `make feed-load-opml` to import a list of RSS feeds from a `feeds.opml` file in the [OPML format](https://en.wikipedia.org/wiki/OPML).

There are analogous `make feed-dump` and `make feed-dump-opml` targets to export feed data from the app.

### Feed parsing

The app works by [periodically](https://github.com/facundoolano/feedi/blob/bf2df4c313e7e719a16d3c2f8216452031a38e58/feedi/config/default.py#L12) fetching
items from different feed sources (RSS/Atom, Mastodon toots and notifications, custom scrapers) and adjusting them to an
[Entry db model](https://github.com/facundoolano/feedi/blob/bf2df4c313e7e719a16d3c2f8216452031a38e58/feedi/models.py#L107) which more or less matches what we expect to display in the front end.

#### RSS/Atom feeds

Most RSS feeds should be processed correctly by the default parser, but sometimes it's desirable to add customizations that cleanup or extend the data for a better look and feel. This can be done by subclassing [feedi.parsers.rss.BaseParser](https://github.com/facundoolano/feedi/blob/4e6b7974b70c70abb4a0f7091adbe344ef0b29a1/feedi/parsers/rss.py#L40). The `is_compatible` static method determines whether a given feed should be parsed with that specific class; the `parse_*` methods overrides the default logic for each field expected in the front end.

As an example, this parser for the lobste.rs link aggregator is adjusted to inline a summary of external link submissions and distinguish between the source article url and the lobste.rs discussion url:

``` python
class LobstersParser(BaseParser):
    def is_compatible(_feed_url, feed_data):
        return 'lobste.rs' in feed_data['feed'].get('link', '')

    def parse_body(self, entry):
        # A 'Comments' link is only present on external link submissions
        if 'Comments' in entry['summary']:
            url = self.parse_content_url(entry)
            return (self.fetch_meta(url, 'og:description') or
                    self.fetch_meta(url, 'description'))
        return entry['summary']

    def parse_entry_url(self, entry):
        # return the discussion url, which is different from entry['link']
        # for external links
        if 'Comments' in entry['summary']:
            soup = BeautifulSoup(entry['summary'], 'lxml')
            return soup.find("a", string="Comments")['href']
        return entry['link']
```

You can see several custom RSS parsers in [this module](https://github.com/facundoolano/feedi/blob/main/feedi/parsers/rss.py).

#### Custom feeds

Other than RSS and Mastodon feeds, the app can ingest arbitrary sources with custom parsers. This is useful for scraping websites that don't provide feeds or consuming JSON APIs directly.

To add a custom parser, subclass [feedi.parsers.custom.CustomParser](https://github.com/facundoolano/feedi/blob/4e6b7974b70c70abb4a0f7091adbe344ef0b29a1/feedi/parsers/custom.py#L20). The `is_compatible` method determines wheter a given url should be parsed with that parser. The `fetch` method does the actual fetching and parsing of entries. See the [feedi.parsers.custom](https://github.com/facundoolano/feedi/blob/HEAD/feedi/parsers/custom.py) module for some examples.

Once the parser is implemented, it will be used when a new feed of type "Custom" is added in the webapp with the expected url.

### Mastodon account setup

One or more Mastodon accounts can be added to ingest the user home feed and notifications.
The account login flow isn't supported in the web interface yet, so some steps need to be run manually
in the python shell to obtain a user access token:

    make shell
    >>> import mastodon
    >>> Mastodon.create_app("feedi", scopes=['read'], to_file='mastodon.creds', api_base_url='https://mastodon.social')

The code above will register a `feedi` app in the mastodon.social server, storing the client and secret in the `mastodon.creds` file.
Note that you don't need to create more than one app per server (even if to plan to log in mutliple times or multiple accounts,
the same app credentials file can be reused).

Once app credentials are available, they can be used to instantiate a client and log in a user to obtain an access token:

    >>> client = Mastodon('mastodon.creds', api_base_url='https://mastodon.social')
    >>> client.log_in(username='some@email.address', password='password', scopes=['read'])
    [CLIENT ACCESS TOKEN PRINTED HERE]

With the resulting access token, you can add the user home feed or the user notification feed from the web UI by accessing
 `/feeds/new` and selecting feed type `Mastodon` or `Mastodon Notifications`. (the same access token can be reused to add
 both feeds).

See the [Mastodon.py documentation](https://mastodonpy.readthedocs.io/en/stable/#usage) for further details.

### Kindle device setup

The app allows to register a kindle device (statically in the configuration, for now) to send the cleaned up article HTML to it. This uses the [stkclient](https://github.com/maxdjohnson/stkclient) library.

To generate a device credentials file:

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

Then update [the config](https://github.com/facundoolano/feedi/blob/a7a0c6e8b13b790cc80b499bb9a9d9a55e8f975b/feedi/config.py#L13-L16) to point to the generated file:

    KINDLE_CREDENTIALS_PATH = 'kindle.creds'


### Keyboard shortcuts

| shortcut                              | when                         | action                              |
| -----------                           | -----------                  | ---------                           |
| /                                     |                              | focus search input                  |
| Enter                                 | search focused               | submit first suggestion             |
| Escape                                | search or suggestion focused | hide suggestions                    |
| Down Arrow, Ctrl+n                    | search or suggestion focused | next suggestion                     |
| Up Arrow, Ctrl+n                      | suggestion focused           | previous suggestion                 |
| Enter                                 | entry focused                | open entry content                  |
| Cmd+Enter, Cmd+Left Click             | entry focused                | open entry content on new tab       |
| Cmd+Shift+Enter, Cmd+Shift+Left Click | entry focused                | open entry discussion on new window |
| Down Arrow, Tab                       | entry focused                | focus next entry                    |
| Up Arrow, Shift+Tab                   | entry focused                | focus previous entry                |
| p                                     | entry focused                | pin entry                           |
| f                                     | entry focused                | favorite entry                      |
| Escape                                | viewing entry content        | go back                             |


### Non-local setup

Not that I claim this to be production-ready, but there's a [setup script](./setup_server.sh) to run it as a service on a Debian Linux, which has been tested on a raspberry pi with Pi OS lite.

### User management

The default app configuration assumes a single-user unauthenticated setup, but authentication can be enabled in case security is necessary,
for example to deploy the app on the internet or to support multiple accounts.

To enable user authentication:

1. Remove the `DEFAULT_AUTH_USER` setting from the [configuration](https://github.com/facundoolano/feedi/blob/HEAD/feedi/config/default.py).
2. If you already have a DB created, reset it with `make dbreset`. Or, alternatively, remove the default user
with `make user-del EMAIL=admin@admin.com`. Note that this will also remove feeds and entries associated to it in the DB.
3. You can create new users by running `make user-add EMAIL=some@email.address`. The command will prompt for a password.

Note that there's no open user registration functionality exposed to the front end, but it should be straightforward to add it if you need it. Check the [auth module](https://github.com/facundoolano/feedi/blob/HEAD/feedi/auth.py) and the [flask-login documentation](https://flask-login.readthedocs.io/en/latest/) for details.

## Design and implementation notes

This project was inspired by the Mastodon web client and the idea of [IndieWeb readers](https://aaronparecki.com/2018/04/20/46/indieweb-reader-my-new-home-on-the-internet), although it's not intended to become either a fully-fledged Mastodon client nor support all the components of an indie reader. I tried to build an interface similar to the Mastodon and Twitter feeds, which feels more intuitive to me than the usual email inbox metaphor of most RSS readers.

I applied a [Boring Tech](https://mcfunley.com/choose-boring-technology) and [Radical Simplicity](https://www.radicalsimpli.city/) mindset when possible; I didn't attempt to make the app scalable or particularly maintainable, I preferred for it to be easy to setup locally and iterate on. I skipped functionality I wouldn't need to use frequently yet (e.g user auth) and focused instead on trying out UX ideas to see how I liked to use the tool myself (still going through that process).

The backend is written in Python using [Flask](flask.palletsprojects.com/). Although I usually default to Postgres for most projects, I opted for sqlite here since it's easier to manage and comes built-in with Python. Some periodic tasks (fetching RSS articles and Mastodon toots, deleting old feed entries) are run using the [Mini-Huey library](https://huey.readthedocs.io/en/latest/contrib.html#mini-huey) in the same Python process as the server. The concurrency is handled by gevent and the production server is configured to run with gunicorn.

The frontend is rendered server-side with [htmx](htmx.org/) for the dynamic fragments (e.g. infinite scrolling, input autocomplete). I tried not to replace native browser features more than necessary. I found that htmx, together with its companion [hyperscript library](hyperscript.org/) were enough to implement anything I needed without a single line of JavaScript, and was surprised by its expressiveness. I'm not sure how it would scale for a bigger project with multiple maintainers, but it certainly felt ideal for this one (I basically picked up front end development where I left it over a decade ago). Here are a couple examples:

``` html
<!-- show a dropdown menu -->
<div class="dropdown-trigger">
    <a class="icon level-item" tabindex="-1"
       _="on click go to middle of the closest .feed-entry smoothly then
              on click toggle .is-active on the closest .dropdown then
              on click elsewhere remove .is-active from the closest .dropdown">
        <i class="fas fa-ellipsis-v"></i>
    </a>
</div>

<!-- show an image on a modal -->
<figure class="image is-5by3 is-clickable" tabindex="-1"
        _="on click add .is-active to the next .modal then halt">
    <img src="{{ entry.media_url }}" alt="article preview">
</figure>

<!-- toggle a setting to display entry thumbnails -->
<label class="checkbox">
    <input type="checkbox" name="hide_media"
           hx-post="/session/hide_media"
           _="on click toggle .is-hidden on .media-url-container">
    Show thumbnails
</label>
```

The CSS is [bulma](bulma.io/), with a bunch of hacky tweaks on top which I'm not particularly proud of.

I (reluctantly) added a dependency on nodejs to use the [mozilla/readability](https://github.com/mozilla/readability) package to show articles in an embedded "reader mode" (skipping ads and bypassing some paywalls). I tried several python alternatives but none worked quite as well as the Mozilla tool. It has the added benefit that extracting articles with them and sending them to a Kindle device produces better results than using Amazon's Send To Kindle browser extension.
