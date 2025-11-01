.PHONY: all deps deps-dev deps-lock run docker docker-flask shell test lint format feed-* prod-* user-* db-*

flask=uv run flask --app feedi/app.py

export FLASK_ENV ?= development

all: uv deps node_modules

.PHONY: uv
uv:
	@command -v uv >/dev/null 2>&1 || \
		(command -v curl >/dev/null 2>&1 && curl -LsSf https://astral.sh/uv/install.sh | sh) || \
		(command -v wget >/dev/null 2>&1 && wget -qO- https://astral.sh/uv/install.sh | sh) || \
		(echo "Error: Neither curl nor wget is available. Cannot install uv." && exit 1)


deps:
	uv sync --no-dev

deps-dev: deps
	uv sync --dev

node_modules:
	npm install || true

# make test
# make test TEST=test_feed_ad
test:
	uv run FLASK_ENV=testing pytest --disable-warnings -v $(if $(TEST),-k $(TEST))

format:
	uv run ruff format

lint:
	uv run ruff check
	uv run ruff format --check

# Serve the app in development mode
run:
	$(flask) run --debug --reload -h 0.0.0.0 -p 9988

# Build a docker for the app container and run it
docker:
	mkdir -p instance
	docker build -t feedi .
	docker run -p 9988:9988 -v ${shell pwd}/instance:/app/instance feedi

docker-flask: CONTAINER_ID=${shell docker ps --filter "ancestor=feedi" -q}
docker-flask:
	docker exec -it $(CONTAINER_ID) /bin/sh -c "FLASK_ENV=development flask --app feedi/app.py $(CMD)"


shell:
	DISABLE_CRON_TASKS=1 $(flask) shell

db-shell:
	sqlite3 -cmd ".open instance/feedi.db"

db-reset:
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

feed-buckets:
	$(flask) feed recalculate-buckets

feed-debug:
	$(flask) feed debug $(URL)

user-add:
	$(flask) user add $(EMAIL)

user-del:
	$(flask) user del $(EMAIL)

# Serve the app in production mode, with gunicorn
prod: feedi/config/production.py
	uv run gunicorn

# Install feedi on a fresh debian server.
# usage:   make prod-install SSH=pi@feedi.local
prod-install:
	ssh $(SSH) 'bash -s' < setup_server.sh

# Update the version running on a remote server (initialized according to setup_server.sh)
BRANCH ?= main
prod-deploy:
	ssh $(SSH) "sudo su feedi -l -c \"cd ~/feedi && make prod-update-code BRANCH=$(BRANCH)\" && sudo systemctl restart gunicorn"

BRANCH ?= main
prod-update-code:
	git stash # because of prod config
	git fetch
	git checkout $(BRANCH)
	git pull origin $(BRANCH) --ff-only
	git stash apply
	make deps
	uv run alembic upgrade head

# one-time generate the production configuration, including the flask app secret key
feedi/config/production.py:
	echo "DEFAULT_AUTH_USER = None \nSECRET_KEY = '$$(python -c 'import secrets; print(secrets.token_hex())')'" >> feedi/config/production.py

# prod-db-push:
# 	scp instance/feedi.db $(SSH):/home/feedi/feedi/instance/feedi.db

prod-db-pull:
	scp $(SSH):/home/feedi/feedi/instance/feedi.db instance/feedi.db

prod-csv-push:
	make feed-dump EMAIL=$(EMAIL)
	scp feeds.csv $(SSH):/home/feedi/feedi/feeds.csv
	git checkout feeds.csv
	ssh $(SSH) "cd /home/feedi/feedi && sudo su feedi -c \"FLASK_ENV=production make feed-load EMAIL=$(EMAIL)\" && sudo su feedi -c \"git checkout feeds.csv\""

prod-csv-pull:
	ssh $(SSH) "cd /home/feedi/feedi && sudo su feedi -c \"FLASK_ENV=production make feed-dump EMAIL=$(EMAIL)\""
	scp $(SSH):/home/feedi/feedi/feeds.csv feeds.csv
	ssh $(SSH) "cd /home/feedi/feedi && sudo su feedi -c \"git checkout feeds.csv\""
	FLASK_ENV=production make feed-load EMAIL=$(EMAIL)
	git checkout feeds.csv
