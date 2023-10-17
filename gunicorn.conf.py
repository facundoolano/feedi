bind = "127.0.0.1:5000"
worker_class = "gevent"
wsgi_app = "feedi.app:create_app()"
raw_env = ["FEEDI_CONFIG=config/production.py"]
preload = True
workers = 2
timeout = 0
