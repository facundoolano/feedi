# Design and implementation notes

## 2023-09-07

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
