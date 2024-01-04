SQLALCHEMY_DATABASE_URI = "sqlite:///feedi.db"

ENTRY_PAGE_SIZE = 10

SYNC_FEEDS_CRON_MINUTES = '*/30'
DELETE_OLD_CRON_HOURS = '*/12'

SKIP_RECENTLY_UPDATED_MINUTES = 10
CONTENT_PREFETCH_MINUTES = '*/15'
RSS_SKIP_OLDER_THAN_DAYS = 7
DELETE_AFTER_DAYS = 7
RSS_MINIMUM_ENTRY_AMOUNT = 5
MASTODON_FETCH_LIMIT = 50

# How many tasks to allow running concurrently. eg. how many feeds to sync at a time.
# This affects the sqlalchemy engine pool size
HUEY_POOL_SIZE = 100

# username to use internally when authentication is "disabled"
# this user will be inserted automatically when first creating the DB
# and auto-logged-in when a browser first sends a request to the app.
DEFAULT_AUTH_USER = 'admin@admin.com'
