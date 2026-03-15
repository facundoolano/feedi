# Restructure feedi: Thin Entrypoints + Service Layer

## Context

The app grew organically, embedding business logic across three entry points: `routes.py` (web), `tasks.py` (background jobs + CLI). Models absorbed orchestration to reduce duplication, but models now do HTTP (via parsers/scraping), which is awkward. Goals:

- Thin entry points: `web.py`, `cli.py`, `tasks.py`
- Two service modules: `feeds.py` and `entries.py` holding shared business logic
- Models become pure SQLAlchemy (ORM, query builders, simple properties — no HTTP)

Style constraints (from STYLE.md and style-guide): avoid shallow pass-through layers; keep the services deep and meaningful, not 1:1 delegators.

---

## New File Structure

```
feedi/
  app.py          -- update imports: routes→web, add cli
  models.py       -- slim down: remove HTTP-doing methods
  web.py          -- renamed from routes.py + absorbs auth.py
  cli.py          -- NEW: all CLI commands (extracted from tasks.py)
  tasks.py        -- background Huey tasks only (calls services)
  email.py        -- unchanged (SMTP infrastructure)
  filters.py      -- unchanged
  services/
    __init__.py
    feeds.py      -- NEW: feed service
    entries.py    -- NEW: entry service; absorbs parsers/html.py + scraping.extract/package_epub
  parsers/
    __init__.py   -- unchanged
    requests.py   -- MOVED from feedi/requests.py
    scraping.py   -- MOVED from feedi/scraping.py (trimmed to CachingRequestsMixin + meta helpers)
    rss.py        -- update import: from . import requests, scraping
    custom.py     -- update import: from . import requests, scraping
    html.py       -- DELETED: absorbed into services/entries.py
  requests.py     -- DELETED (moved to parsers/requests.py)
  scraping.py     -- DELETED (get_favicon inlined into services/feeds.py; rest moved to parsers/scraping.py)
  auth.py         -- DELETED (absorbed into web.py)
```

**Rationale for grouping decisions:**
- `services/`: conventional, makes architectural role explicit; 2 domain-operation modules
- No `interfaces/` directory: entrypoints (web, tasks, cli) are already clearly named at the top level; wrapping them adds nesting with no benefit
- `parsers/` expanded: requests and scraping utilities are parsing infrastructure; keeping them close to their callers
- `auth.py` → `web.py`: auth is 66 lines of web routes + Flask-Login setup, belongs with the other web code (locality of behavior)

---

## Step 1: Create `feedi/services/feeds.py`

Extract from `models.py` and `tasks.py`:

- `sync(feed, force=False)` — logic from `Feed.sync_with_remote()`: cooldown check, call type-specific fetch, upsert entries, update bucket
- `fetch_entry_data(feed, force=False)` — type-dispatch replacing the polymorphic model methods; calls `parsers.rss.fetch(...)` for RssFeed, `parsers.custom.fetch(...)` for CustomFeed; updates `feed.etag`, `feed.modified_header`, `feed.raw_data` (for RssFeed)
- `load_icon(feed)` — type-dispatch: `parsers.rss.fetch_icon()` for RssFeed, `scraping.get_favicon()` for others
- `add(user_id, type, name, url, **values)` — from `routes.feed_add_submit()`: create feed, `load_icon`, `db.session.add/flush/commit`, return feed
- `delete(feed)` — from `routes.feed_delete()`: preserve pinned/favorited entries, delete feed
- `discover(url)` — thin wrapper around `parsers.rss.discover_feed(url)`
- `import_csv(user, file)` / `export_csv(user, file)` — from `tasks.csv_load` / `tasks.csv_dump`
- `import_opml(user, file)` / `export_opml(user, file)` — from `tasks.opml_load` / `tasks.opml_dump`
- `recalculate_buckets()` — from `tasks.recalculate_buckets`
- `purge_old()` — logic from `tasks.delete_old_entries()`
- `_add_if_not_exists(feed)` — helper extracted from `tasks.add_if_not_exists()`

## Step 2: Create `feedi/services/entries.py`

Extract from `models.py`, `routes.py`, and absorb `parsers/html.py` and `scraping.extract()`/`package_epub()`:

- `get_page(user_id, page_arg, hide_seen, is_mixed, **filters)` — logic from `routes.fetch_entries_page()`: cursor pagination, auto-mark-read, return `(entry_page, next_page)`
- `fetch_content(entry)` — from `Entry.fetch_content()`: calls `_extract()` helper (see below)
- `from_url(user_id, url)` — from `Entry.from_url()`: look up existing or call `_parse_html_entry()` (see below)
- `send_to_kindle(user, url)` — from `routes.send_to_kindle()`: calls `_extract()` + `_package_epub()`, sends via email, saves/updates entry record
- `prefetch_content()` — body from `tasks.content_prefetch()`
- `_extract(url)` — moved from `scraping.extract()`: calls Firefox readability subprocess, processes content; only called from within entries.py
- `_package_epub(url, article)` — moved from `scraping.package_epub()`: only called for Kindle delivery; only called from within entries.py
- `_parse_html_entry(url)` — moved from `parsers/html.py`'s `fetch()`: parses article metadata from a URL to create a standalone entry dict; only called from `from_url()`

## Step 2b: Consolidate parsing infrastructure

- Move `feedi/requests.py` → `feedi/parsers/requests.py`
- Move `feedi/scraping.py` → `feedi/parsers/scraping.py`, removing `extract()` and `package_epub()` (absorbed into `services/entries.py`) and `get_favicon()` (inlined into `services/feeds.py`)
- Update `parsers/rss.py` and `parsers/custom.py` imports: `from feedi import requests, scraping` → `from . import requests, scraping`
- Delete `feedi/parsers/html.py` (absorbed into `services/entries.py`)
- Delete `feedi/requests.py` and `feedi/scraping.py` (replaced by parsers-internal versions)

## Step 3: Slim down `feedi/models.py`

Remove (moved to services):
- `Feed.sync_with_remote()` and `Feed._calculate_bucket_from_db()` → `feeds.sync()` / private helper in feeds.py
- `Feed.load_icon()` (both on Feed and RssFeed) → `feeds.load_icon()`
- `RssFeed.fetch_entry_data()` and `CustomFeed.fetch_entry_data()` → `feeds.fetch_entry_data()`
- `Entry.from_url()` → `entries.from_url()`
- `Entry.fetch_content()` → `entries.fetch_content()`
- Remove `import feedi.parsers` and `from feedi import scraping` from models.py

Keep in models.py:
- All model classes, columns, relationships
- `Feed.resolve()`, `from_valuelist()`, `to_valuelist()`, `RssFeed.from_valuelist()/to_valuelist()`
- `Entry.filter_by()`, `_filtered_query()`, `select_pinned()` — query builders are tightly coupled to schema, not business logic
- `Entry.is_external_link`, `Entry.has_distinct_user` — pure property computations

## Step 4: Create `feedi/cli.py`

Extract from `tasks.py` and register all CLI commands here:

```python
feed_cli = flask.cli.AppGroup("feed")
user_cli = flask.cli.AppGroup("user")

def register(app):
    app.cli.add_command(feed_cli)
    app.cli.add_command(user_cli)
```

Commands:
- `feed sync` — calls `tasks.sync_all_feeds().get()` (dispatches Huey task and waits)
- `feed prefetch` — calls `tasks.content_prefetch().get()`
- `feed purge` — calls `tasks.delete_old_entries().get()`
- `feed debug <url>` — calls `parsers.rss.pretty_print(url)`
- `feed load <file> [user]` — calls `feeds.import_csv(user, file)`
- `feed dump <file> [user]` — calls `feeds.export_csv(user, file)`
- `feed load-opml <file> [user]` — calls `feeds.import_opml(user, file)`
- `feed dump-opml <file> [user]` — calls `feeds.export_opml(user, file)`
- `feed recalculate-buckets` — calls `feeds.recalculate_buckets()`
- `user add <email>` — creates User directly (no service needed)
- `user del <email>` — deletes User directly (no service needed)
- `load_user_arg()` callback stays in cli.py

## Step 5: Slim down `feedi/tasks.py`

Remove: all CLI commands, `feed_cli`/`user_cli` definitions, `add_if_not_exists`, `load_user_arg`

Keep: Huey init, `create_huey_app()` import, `huey_task` decorator, and these pure Huey background tasks (each calls a service):

```python
@huey_task(crontab(...))
def sync_all_feeds():
    feeds.sync_all()   # or inline: query feeds and dispatch sync_feed subtasks

@huey_task()
def sync_feed(feed_id, _feed_name, force=False):
    feed = db.session.get(models.Feed, feed_id)
    feeds.sync(feed, force=force)
    db.session.commit()

@huey_task(crontab(...))
def content_prefetch():
    entries.prefetch_content()

@huey_task(crontab(...))
def delete_old_entries():
    feeds.purge_old()
```

## Step 6: Rename `routes.py` → `web.py`; absorb `auth.py`; thin out routes

- Rename file; update `app.py` import
- Move auth logic from `auth.py` into `web.py`: Flask-Login setup (`login_manager`, `load_user`), `/auth/login` routes, `/auth/kindle` routes — remove `auth.py` and its `init()` call in `app.py`
- Remove `fetch_entries_page()` (moved to `services.entries.get_page()`)
- Remove `send_to_kindle()` body inline (moved to `services.entries.send_to_kindle()`)
- Route functions become thin: parse request args → call service → render template or redirect
- `entry_pin()` calls `services.entries.fetch_content(entry)`
- `feed_add_submit()` calls `services.feeds.add()`
- `feed_delete()` calls `services.feeds.delete(feed)`
- `feed_add()` calls `services.feeds.discover(url)`

## Step 7: Update `feedi/app.py`

```python
# Change:
from . import auth, filters, routes, tasks
# To:
from . import filters, web, tasks, cli
# ...
cli.register(app)   # register CLI groups
```

- Drop `auth` import (auth is now part of `web.py`)
- Drop `auth.init()` call
- `tasks.py` no longer self-registers CLI groups (that's cli.py's job)

---

## Critical Files

| File | Action |
|------|--------|
| `feedi/models.py` | Remove HTTP-doing methods; keep ORM + query builders |
| `feedi/routes.py` → `feedi/web.py` | Rename; absorb auth.py; thin out routes |
| `feedi/auth.py` | DELETE after absorbed into web.py |
| `feedi/tasks.py` | Remove CLI + CLI group registration; keep Huey tasks |
| `feedi/cli.py` | NEW: all CLI commands |
| `feedi/app.py` | Update imports; drop auth.init() call |
| `feedi/services/__init__.py` | NEW: empty package init |
| `feedi/services/feeds.py` | NEW: feed service |
| `feedi/services/entries.py` | NEW: entry service (absorbs html parser + article extraction) |
| `feedi/scraping.py` | DELETE (split: utils→parsers/scraping.py, get_favicon→services/feeds.py) |
| `feedi/requests.py` | DELETE (moved to parsers/requests.py) |
| `feedi/parsers/requests.py` | NEW: moved from feedi/requests.py |
| `feedi/parsers/scraping.py` | NEW: moved from feedi/scraping.py (sans extract/package_epub/get_favicon) |
| `feedi/parsers/html.py` | DELETE after absorbed into services/entries.py |
| `feedi/parsers/rss.py` | Update imports to use local requests/scraping |
| `feedi/parsers/custom.py` | Update imports to use local requests/scraping |

---

## Verification

1. `make run` — app starts without errors
2. `make shell` — can import `from feedi.services import feeds, entries` without errors
3. `make feed-sync` — syncs feeds via CLI
4. `make feed-load EMAIL=...` — CSV import works
5. `make user-add EMAIL=...` — user creation works
6. Web: add a feed → entries appear; pin/favorite entries; send to Kindle; view entry content
7. Cron tasks: `DISABLE_CRON_TASKS` unset, check that Huey starts and syncs periodically
8. `make lint` — no import errors or ruff violations
