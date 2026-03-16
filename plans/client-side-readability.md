# Remove Node.js / Move Readability to Client Side

## Context

The app has a Node.js dependency solely to run `@mozilla/readability` in a server-side subprocess (`feedi/extract_article.js`). This adds operational weight: `node_modules`, a multi-stage Dockerfile, Node.js installation in `setup_server.sh`, and a Makefile `npm install` step.

Mozilla Readability is designed for browser use. By moving processing to the client we eliminate Node.js while keeping reader mode and Kindle integration. The trade-off is losing background content prefetching (user accepts this).

**New flow:**
1. User opens entry → server renders shell instantly (no slow fetch)
2. Hyperscript `on revealed` triggers JS `loadContent()`
3. JS fetches raw HTML from server proxy endpoint, runs Readability + post-processing
4. JS renders content and POSTs cleaned article back for caching
5. Kindle: hyperscript `on click ... await sendToKindle()` → JS fetches+processes (or reuses cached) → server packages EPUB and emails

**Frontend pattern:** hyperscript handles event wiring and UI feedback (spinner); JS handles Readability logic; HTMX is not used for these interactions (HTMX's `htmx:beforeSwap` interception would work but is fragile with nested element targeting).

---

## Files to Modify

| File | Change |
|------|--------|
| `feedi/web.py` | Add 2 new endpoints; simplify `entry_view`; modify `send_to_kindle` |
| `feedi/services/entries.py` | Remove `_extract()`, `fetch_content()`; modify `send_to_kindle` signature |
| `feedi/tasks.py` | Remove `content_prefetch` task |
| `feedi/config/default.py` | Remove `CONTENT_PREFETCH_MINUTES` |
| `feedi/templates/entry_content.html` | Replace HTMX trigger with hyperscript+JS |
| `feedi/templates/entry_commands.html` | Replace Kindle `hx-post` with hyperscript+JS |
| `Makefile` | Remove `node_modules` target; add `update-readability` target |
| `Dockerfile` | Remove Node.js multi-stage build |
| `setup_server.sh` | Remove Node.js installation block |

## Files to Add

- `feedi/static/js/readability.js` — downloaded via `make update-readability` (curl from unpkg); vendored into the repo so no runtime download needed

## Files to Delete

- `feedi/extract_article.js`
- `package.json`
- `package-lock.json`
- `node_modules/`

---

## Backend Changes

### 1. New endpoint: `GET /entries/<id>/article`

Proxy raw HTML from `entry.content_url` to the client for Readability processing. Named `article` to avoid confusion with the existing `/entries/<id>/debug` endpoint that returns raw entry JSON.

```python
@app.get("/entries/<int:id>/article")
@login_required
def entry_article(id):
    """Proxy the source HTML of the entry URL for client-side Readability processing."""
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)
    if not entry.content_url:
        flask.abort(400)
    response = requests.get(entry.content_url)
    response.raise_for_status()
    return flask.Response(response.content, content_type='text/html')
```

### 2. New endpoint: `POST /entries/<id>/content`

Cache the client-processed article. Also marks entry as viewed.

```python
@app.post("/entries/<int:id>/content")
@login_required
def entry_save_content(id):
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)
    if not entry.content_full:
        entry.content_full = flask.request.json["content"]
        entry.viewed = entry.viewed or datetime.datetime.utcnow()
        db.session.commit()
    return "", 204
```

### 3. Simplify `entry_view` (`GET /entries/<id>`)

Remove the two-phase HTMX loading logic (the `?content=true` param and `_extract()` call). Just render the template — JS handles fetching when `content` is None.

Also remove `entries.fetch_content(entry)` call from `entry_pin()` (line 122 of web.py).

```python
@app.get("/entries/<int:id>")
@login_required
def entry_view(id):
    entry = db.get_or_404(models.Entry, id)
    if entry.user_id != current_user.id:
        flask.abort(404)
    if not entry.content_url and not entry.target_url:
        return "Entry not readable", 400
    if "youtube.com" in entry.content_url or "vimeo.com" in entry.content_url:
        return _redirect_response(entry.target_url)
    if entry.content_full:
        entry.viewed = entry.viewed or datetime.datetime.utcnow()
        db.session.commit()
    return flask.render_template("entry_content.html", entry=entry, content=entry.content_full)
```

### 4. Modify `POST /entries/kindle`

Accept JSON body with pre-processed article. The client sends `{url, content, title, byline, siteName, publishedTime, lang}`.

```python
@app.post("/entries/kindle")
@login_required
def send_to_kindle():
    if not current_user.kindle_email:
        return "", 204
    data = flask.request.json
    url = data["url"]
    article = {k: data.get(k) for k in ["content", "title", "byline", "siteName", "publishedTime", "lang"]}
    entries.send_to_kindle(current_user, url, article)
    return "", 204
```

### 5. `feedi/services/entries.py`

- **Remove** `_extract()` (subprocess, Node.js calls) and `fetch_content()`
- **Remove** `import subprocess`
- **Modify** `send_to_kindle(user, url, article)` to accept pre-processed article dict:

```python
def send_to_kindle(user, url, article):
    """Package article as epub and send to Kindle. Records the entry."""
    attach_data = _package_epub(url, article)
    feedi_email.send(user.kindle_email, attach_data, filename=article["title"])

    entry = get_from_url(user.id, url)
    entry.sent_to_kindle = datetime.datetime.now()
    entry.viewed = entry.viewed or datetime.datetime.utcnow()
    entry.content_full = article["content"]
    db.session.add(entry)
    db.session.commit()
```

`_package_epub()` and all BeautifulSoup usage in it remain unchanged.

### 6. `feedi/tasks.py`

Remove the `content_prefetch()` task (lines 75–88).

### 7. `feedi/config/default.py`

Remove `CONTENT_PREFETCH_MINUTES = "*/15"`.

---

## Frontend Changes

### 8. `feedi/static/js/readability.js`

Vendored copy of Readability.js, managed by `make update-readability`. Included globally in `base.html` (needed for both reader view and Kindle from list view).

### 9. `feedi/templates/entry_content.html`

Replace the HTMX loading block with a hyperscript trigger calling a JS function:

**Current** (remove):
```html
<div class="buttons is-centered"
     hx-get="{{url_for('entry_view', id=entry.id, content='true' )}}"
     hx-trigger="revealed"
     hx-swap="outerHTML"
     hx-select=".entry-content"
     hx-target=".entry-content">
    <br/>
    <button class="button is-loading is-large is-centered" style="border: none;">Button</button>
</div>
```

**New** (replace with):
```html
<div class="buttons is-centered"
     _="on revealed call loadContent('{{ url_for('entry_article', id=entry.id) }}',
                                      '{{ entry.content_url }}',
                                      '{{ url_for('entry_save_content', id=entry.id) }}')">
    <br/>
    <button class="button is-loading is-large is-centered" style="border: none;">Button</button>
</div>
```

The spinner remains visible until `loadContent()` replaces `.entry-content`'s innerHTML.

Add to `entry_content.html` (inline `<script>`):

```javascript
async function loadContent(articleUrl, contentUrl, saveUrl) {
    try {
        const resp = await fetch(articleUrl);
        const html = await resp.text();

        const doc = new DOMParser().parseFromString(html, 'text/html');
        // Set base for relative URL resolution
        const base = doc.createElement('base');
        base.href = contentUrl;
        doc.head.insertBefore(base, doc.head.firstChild);

        const article = new Readability(doc).parse();
        if (!article) throw new Error('parse failed');

        // Preserve Python post-processing: lazy images → src, strip iframe heights
        const tmp = document.createElement('div');
        tmp.innerHTML = article.content;
        for (const attr of ['data-src', 'data-lazy-src', 'data-td-src-property', 'data-srcset']) {
            tmp.querySelectorAll(`img[${attr}]`).forEach(img => {
                const src = img.getAttribute(attr);
                while (img.attributes.length) img.removeAttribute(img.attributes[0].name);
                img.src = src;
            });
        }
        tmp.querySelectorAll('iframe[height]').forEach(el => el.removeAttribute('height'));
        article.content = tmp.innerHTML;

        // Store for Kindle reuse
        window._currentArticle = article;
        window._currentArticleUrl = contentUrl;

        document.querySelector('.entry-content').innerHTML = article.content;

        // Cache on server (fire and forget)
        fetch(saveUrl, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({content: article.content})
        });
    } catch(e) {
        document.querySelector('.entry-content').innerHTML =
            `<p><a href="${contentUrl}" target="_blank">Open in browser</a></p>`;
    }
}
```

### 10. `feedi/templates/entry_commands.html`

Replace the Kindle HTMX button with hyperscript+JS:

**Current** (remove):
```html
<a class="dropdown-item"
   hx-post="{{ url_for('send_to_kindle', url=entry.content_url ) }}"
   _="on htmx:beforeRequest or htmx:afterRequest toggle .fa-spin on <i/> in me"
><span class="icon"><i class="fas fa-tablet-alt"></i></span> Send to Kindle</a>
```

**New**:
```html
<a class="dropdown-item"
   _="on click toggle .fa-spin on <i/> in me
      then await sendToKindle({{ entry.id }}, '{{ entry.content_url }}')
      then toggle .fa-spin on <i/> in me"
><span class="icon"><i class="fas fa-tablet-alt"></i></span> Send to Kindle</a>
```

Add `sendToKindle` as an inline `<script>` in `entry_commands.html`:

```javascript
async function sendToKindle(entryId, contentUrl) {
    let article;
    if (window._currentArticle && window._currentArticleUrl === contentUrl) {
        article = window._currentArticle;
    } else {
        const resp = await fetch(`/entries/${entryId}/article`);
        const html = await resp.text();
        const doc = new DOMParser().parseFromString(html, 'text/html');
        const base = doc.createElement('base');
        base.href = contentUrl;
        doc.head.insertBefore(base, doc.head.firstChild);
        article = new Readability(doc).parse();
    }
    await fetch('/entries/kindle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            url: contentUrl,
            content: article.content,
            title: article.title,
            byline: article.byline,
            siteName: article.siteName,
            publishedTime: article.publishedTime,
            lang: article.lang
        })
    });
}
```

Since `entry_commands.html` is included from both the entry list and reader view, `readability.js` must be loaded globally — add to `base.html`.

---

## Infrastructure Cleanup

### Makefile

```makefile
# Change:
all: uv deps node_modules
# To:
all: uv deps

# Remove the node_modules: target entirely

# Add:
update-readability:
    curl -o feedi/static/js/readability.js https://unpkg.com/@mozilla/readability/Readability.js
```

### Dockerfile

Remove the Node.js multi-stage build:
- Remove `FROM node:20-alpine AS node`
- Remove all four `COPY --from=node ...` lines
- Remove `COPY package*.json ./` and `RUN npm ci --omit=dev`

### setup_server.sh

Remove the Node.js installation block (lines 17–23: `ca-certificates`, `nodesource` GPG key, `apt install nodejs`).

---

## What's Lost

- **Content prefetching** — background task removed; content loads on first reader view (acknowledged)
- **`entry_pin` content pre-fetch** — `fetch_content()` call removed from `entry_pin()`; pinning still works, just won't pre-cache content

## What's Preserved

- Reader mode (now client-side, same quality via Readability)
- All BeautifulSoup post-processing: lazy image attrs → src (data-src, data-lazy-src, data-td-src-property, data-srcset), iframe height removal — reproduced in JS
- Content caching: once processed, `content_full` is stored and rendered server-side on revisit
- Kindle integration: article metadata (title, byline, siteName, publishedTime, lang) passed from client for EPUB generation
- `_package_epub()` — unchanged
- `sanitize_content` filter — unchanged (only used for `content_short` in list view)

---

## Verification

1. `make run` — app starts, no Node.js errors
2. Open an entry with `content_url` → reader view → spinner shows → content appears
3. Refresh entry → content renders immediately (from cache, no JS needed)
4. Click "Send to Kindle" from list view → spinner on icon → disappears → entry has `sent_to_kindle`
5. Open reader view then click "Send to Kindle" → reuses `window._currentArticle` (no second fetch)
6. Confirm `extract_article.js`, `package.json`, `node_modules/` are gone
7. `make test` passes
