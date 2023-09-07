# feedi

feedi is a personal web RSS reader that also works as a (read-only) Mastodon client.

TODO add a screenshots

The project is currently experimental and is missing some pieces (and it will likely remain that way for a while),
but feel free to try and hack on it. More details on the [design notes](#design-and-implementation-notes) below.

## Local setup

Requires Python 3 (tested with 3.9 and 3.11) and nodejs (for the reader functionality; tested with node 20).

To install on a local virtual env run:

    make deps

Then a development server can be run at http://localhost:5000 with:

    make dev

A production-like server can also be run at http://localhost:5000 with:

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

### Feed parsing

The app works by [periodically](https://github.com/facundoolano/feedi/blob/bf2df4c313e7e719a16d3c2f8216452031a38e58/feedi/config/default.py#L12) fetching RSS feed entries and Mastodon toots and adjusting them to an [Entry db model](https://github.com/facundoolano/feedi/blob/bf2df4c313e7e719a16d3c2f8216452031a38e58/feedi/models.py#L107) which more or less matches what we expect to display in the front end.

Most RSS feeds should be processed correctly with the default parser, but sometimes it's desirable to add a custom parser to cleanup or extend the data for a better look and feel. This can be done by subclassing [feedi.sources.rss.BaseParser](https://github.com/facundoolano/feedi/blob/bf2df4c313e7e719a16d3c2f8216452031a38e58/feedi/sources/rss.py#L46). The `is_compatible` static method determines whether a given feed should be parsed with that specific class; the `parse_*` methods overrides the default logic for each field expected in the front end.

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


### Mastodon account setup

One or more Mastodon accounts can be added to ingest the user home feed into the app.
The account login flow isn't supported in the web interface yet though, so to use the feature in the time being
one needs to:

* Register a mastodon app on the server the account belongs to. The same app can be reused for multiple accounts in that server.
* Login with the account to obtain a user access token
* Ingesting the feed in the csv as shown in a previous section (`mastodon,$NAME,$SERVER,$ACCES_TOKEN`)

See the [Mastodon.py documentation](https://mastodonpy.readthedocs.io/en/stable/#usage) for details.

(Ingesting user notifications is a planned feature).

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

| binding                               | when                         | description                         |
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
| Delete, Backspace                     | entry focused                | delete entry                        |
| p                                     | entry focused                | pin entry                           |
| f                                     | entry focused                | favorite entry                      |
| Escape                                | viewing entry content        | go back                             |


### Running in a server

Not that I claim this to be production-ready, but there's a [setup script](./setup_server.sh) to run it as a service on a Debian Linux, which has been tested on a raspberry pi with Pi OS lite.

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
