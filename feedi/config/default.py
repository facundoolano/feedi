SECRET_KEY = b'\xffN\xcfX\xbc\xa9V\x8b*_zFB\xb9\xfa\x1d'
SQLALCHEMY_DATABASE_URI = "sqlite:///feedi.db"
# SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 15}}
DEBUG = True
TEMPLATES_AUTO_RELOAD = True

RSS_SKIP_RECENTLY_UPDATED_MINUTES = 15
RSS_SKIP_OLDER_THAN_DAYS = 7
DELETE_AFTER_DAYS = 7
RSS_MINIMUM_ENTRY_AMOUNT = 5
MASTODON_FETCH_LIMIT = 50

SYNC_FEEDS_CRON_MINUTES = '*/15'
DELETE_OLD_CRON_HOURS = '*/12'

# this is a hack to get personal kindle integration (see readme)
# a real implementation would require some way for the user to set up this integration
# from the web, or at least some make target to make the setup reproducible
KINDLE_CREDENTIALS_PATH = 'kindle.creds'

# how much to wait for the headless browser load a page when extracting js enabled articles
JS_LOADING_DELAY_MS = 1000
