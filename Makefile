# SlideLite developer tasks.  Runtime needs only system packages:
#   sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1
PYTHON ?= python3
VENV   ?= .venv
VERSION := $(shell $(PYTHON) -c "import slidelite; print(slidelite.__version__)")

.PHONY: run dev test test-js lint format check deb clean venv fixtures

run:
	$(PYTHON) bin/slidelite $(FILE)

dev:            ## serve the UI on http://127.0.0.1:8765 for browser debugging
	$(PYTHON) -m slidelite.devserver $(FILE)

venv:           ## dev tools (pytest, ruff, python-pptx for fixtures)
	$(PYTHON) -m venv --system-site-packages $(VENV)
	$(VENV)/bin/pip install -r requirements-dev.txt

test:
	$(PYTHON) -m pytest

test-js:
	node --test tests/js/

lint:
	ruff check slidelite tests tools
	ruff format --check slidelite tests tools

format:
	ruff format slidelite tests tools
	ruff check --fix slidelite tests tools

check: lint test test-js

fixtures:       ## regenerate the test presentation collection
	$(PYTHON) tools/make_fixtures.py tests/fixtures

deb:
	packaging/build-deb.sh

clean:
	rm -rf build dist *.deb .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
