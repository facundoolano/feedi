.PHONY: deps dev-deps shell serve dbreset dbshell feed-load feed-sync feed-debug

venv=. venv/bin/activate &&
flask=$(venv) flask --app feedi/app.py

venv:
	python -m venv venv

deps: venv
	$(venv) pip install -r requirements.txt && npm install

deps-dev: deps
	$(venv) pip install ipython ipdb

dev:
	$(flask) run --debug --reload

shell:
	$(venv) ipython

dbshell:
	sqlite3 -cmd ".open instance/feedi.db"

dbreset:
	rm instance/feedi.db

feed-load:
	$(flask) feed load feeds.csv

feed-sync:
	$(flask) feed sync

feed-debug:
	$(flask) feed debug $(URL)

# TODO move the details to a gunicorn config file
prod: FEEDI_CONFIG='feedi/config/prod.py'
prod:
	$(venv) gunicorn -b 127.0.0.1:5000 -k gevent 'feedi.app:create_app()'

prod-update:
	git checkout main
	git pull origin main --ff-only
	make deps
	$(venv) alembic upgrade head
	sudo systemctl restart gunicorn

secret-key:
	echo "SECRET_KEY = $(python -c 'import secrets; print(secrets.token_hex())')" >> feedi/config/prod.py

rpi-db-push:
	scp instance/feedi.db pi@raspberrypi.local:feedi/instance/feedi.db

rpi-db-pull:
	scp pi@raspberrypi.local:feedi/instance/feedi.db  instance/feedi.db
