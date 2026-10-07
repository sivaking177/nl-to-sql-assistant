# Common commands. Assumes the project venv is at ../.venv (shared across
# the two projects in the workspace) or a local .venv; override with
# PYTHON=... if yours lives elsewhere.
PYTHON ?= $(shell [ -x ../.venv/bin/python ] && echo ../.venv/bin/python || echo .venv/bin/python)

.PHONY: test eval ask serve lint types

test:
	$(PYTHON) -m pytest -q

eval:
	$(PYTHON) -m nlsql.cli eval

ask:
	$(PYTHON) -m nlsql.cli ask "How many trips were paid for by credit card?"

serve:
	$(PYTHON) -m uvicorn nlsql.api.service:app --port 8000

lint:
	$(PYTHON) -m black --check nlsql tests
	$(PYTHON) -m ruff check nlsql tests

types:
	$(PYTHON) -m mypy nlsql
