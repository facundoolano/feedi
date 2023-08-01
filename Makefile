.PHONY: deps dev-deps shell serve reset feeds

venv=source venv/bin/activate &&
flask=$(venv) flask --app feedi/app.py

venv:
	python -m venv venv

deps:
	$(venv) pip install -r requirements.txt

deps-dev: deps
	$(venv) pip install ipython

shell:
	$(venv) ipython

serve:
	$(flask) run --debug --reload

reset:
	rm instance/feedi.db

feed-load:
	$(flask) feed load feeds.csv

feed-delete:
	$(flask) feed delete $(FEED)

feed-sync:
	$(flask) feed sync

feed-debug:
	$(flask) feed debug $(URL)
