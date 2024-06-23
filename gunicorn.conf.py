bind = "127.0.0.1:9988"
worker_class = "gevent"
wsgi_app = "feedi.app:create_app()"
raw_env = ["FLASK_ENV=production"]
preload = True
workers = 1
timeout = 0
