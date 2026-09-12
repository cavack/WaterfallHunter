SHELL := /bin/bash
PYTHON ?= python3
VENV_PYTHON := .venv/bin/python
DB_PATH ?= /srv/waterfallhunter/data/waterfall_registry.db

.PHONY: help setup check-python test typecheck build validate clean-install-check status logs backup-check migration-rehearsal

help:
	@printf '%s\n' 'setup test typecheck build validate clean-install-check status logs backup-check migration-rehearsal'

check-python:
	@$(PYTHON) scripts/check_python_runtime.py

setup: check-python
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r backend/requirements.lock
	npm --prefix frontend ci

test:
	PYTHONPATH=backend/src:. $(VENV_PYTHON) -m pytest -q backend/tests

typecheck:
	npm --prefix frontend run typecheck

build:
	npm --prefix frontend run build

validate:
	$(VENV_PYTHON) scripts/verify_repository_hygiene.py --root .
	$(VENV_PYTHON) scripts/validate_wfh_skills.py
	PYTHONPATH=backend/src:. $(VENV_PYTHON) scripts/verify_runtime_parity.py
	$(MAKE) test
	$(MAKE) typecheck
	$(MAKE) build

clean-install-check:
	./scripts/validate_clean_install.sh

status:
	docker compose ps

logs:
	docker compose logs --tail=200

backup-check:
	DB_PATH='$(DB_PATH)' $(VENV_PYTHON) -c "import os,sqlite3; p=os.environ['DB_PATH']; c=sqlite3.connect('file:'+p+'?mode=ro', uri=True); print(c.execute('PRAGMA integrity_check').fetchone()[0])"

migration-rehearsal:
	$(VENV_PYTHON) scripts/rehearse_sqlite_migration.py --help
