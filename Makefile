# Developer shortcuts. Run them from this folder after: .venv/bin/pip install -r requirements-dev.txt
PY ?= .venv/bin/python

.PHONY: test lint check run

test:  ## run the automated tests
	$(PY) -m pytest -q

lint:  ## check code style and common mistakes
	$(PY) -m ruff check .
	$(dir $(PY))shellcheck *.sh
	node --check static/app.js

check: lint test  ## everything CI runs

run:  ## start the app (same as ./start.sh)
	./start.sh
