SECRET_KEY = b'\xffN\xcfX\xbc\xa9V\x8b*_zFB\xb9\xfa\x1d'
SQLALCHEMY_DATABASE_URI = "sqlite:///feedi.db"
# SQLALCHEMY_ENGINE_OPTIONS = {'connect_args': {'timeout': 15}}
DEBUG = True
TEMPLATES_AUTO_RELOAD = True

RSS_SKIP_RECENTLY_UPDATED_MINUTES = 15
RSS_SKIP_OLDER_THAN_DAYS = 5
RSS_FIRST_LOAD_AMOUNT = 5
MASTODON_FETCH_LIMIT = 50

SYNC_FEEDS_CRON_MINUTES = '*/15'

# this is a hack to get personal kindle integration (see readme)
# a real implementation would require some way for the user to set up this integration
# from the web, or at least some make target to make the setup reproducible
KINDLE_CREDENTIALS_PATH = 'kindle.creds'
