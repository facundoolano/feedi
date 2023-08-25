.PHONY: venv deps dev-deps shell serve dbreset dbshell feed-load feed-sync feed-debug

venv=source venv/bin/activate &&
flask=$(venv) flask --app feedi/app.py

venv:
	python -m venv venv

deps:
	$(venv) pip install -r requirements.txt && npm install

deps-dev: deps
	$(venv) pip install ipython

serve:
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
