.PHONY: deps dev-deps shell serve dbreset dbshell feed-load feed-sync feed-debug

venv=. venv/bin/activate &&
flask=$(venv) flask --app feedi/app.py

venv:
	python -m venv venv

deps: venv
	$(venv) pip install -r requirements.txt && npm install

deps-dev: deps
	$(venv) pip install ipython ipdb flask-shell-ipython

dev:
	$(flask) run --debug --reload

shell:
	DISABLE_CRON_TASKS=1 $(flask) shell

dbshell:
	sqlite3 -cmd ".open instance/feedi.db"

dbreset:
	rm instance/feedi.db

feed-load:
	$(flask) feed load feeds.csv $(EMAIL)

feed-dump:
	$(flask) feed dump feeds.csv $(EMAIL)

feed-load-opml:
	$(flask) feed load-opml feeds.opml $(EMAIL)

feed-dump-opml:
	$(flask) feed dump-opml feeds.opml $(EMAIL)

feed-sync:
	$(flask) feed sync

feed-debug:
	$(flask) feed debug $(URL)

user-add:
	$(flask) user add $(EMAIL)

user-del:
	$(flask) user del $(EMAIL)

prod:
	$(venv) gunicorn

# FIXME this is hacky
BRANCH ?= main
prod-update:
	git stash # because of prod config
	git fetch
	git checkout $(BRANCH)
	git pull origin $(BRANCH) --ff-only
	git stash apply
	make deps
	$(venv) alembic upgrade head
	sudo systemctl restart gunicorn

secret-key:
	echo "SECRET_KEY = '$$(python -c 'import secrets; print(secrets.token_hex())')'" >> feedi/config/prod.py

prod-db-push:
	scp instance/feedi.db pi@feedi.local:feedi/instance/feedi.db

prod-db-pull:
	scp pi@feedi.local:feedi/instance/feedi.db  instance/feedi.db
