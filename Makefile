# Oddtown local dev. Every target is safe to run again.
#
#   make dev        install what's missing, start Docker infra if .env needs it,
#                   start the observer, world service and UI, then check them all
#   make dev WORLD=mock   the scripted mock run instead of real characters (no LLM, no Docker)
#   make dev TRANSPORT=http REGISTRY=file   real characters without Docker. Any .env setting can be overridden this way.
#   make restart    stop, then dev again in the same mode, to pick up Python changes
#   make stop       stop the three services (Docker stays up, see infra-down)
#   make status     health of everything
#   make smoke      status, then one real run until the town is online, then stop it
#   make logs       follow the service logs
#   make test       Python tests, UI tests and the UI type check

SHELL := /bin/bash
VENV := .venv
PY := $(VENV)/bin/python
DEV := .oddtown/dev

.PHONY: dev restart stop status verify smoke logs test test-py test-ui infra-down setup clean-dev

dev: setup
	@scripts/dev.sh start

restart: setup
	@scripts/dev.sh restart

stop:
	@scripts/dev.sh stop

status verify:
	@scripts/dev.sh status

smoke:
	@scripts/dev.sh smoke

logs:
	@mkdir -p $(DEV) && touch $(DEV)/observer.log $(DEV)/world.log $(DEV)/ui.log
	tail -n 30 -F $(DEV)/observer.log $(DEV)/world.log $(DEV)/ui.log

test: test-py test-ui

test-py: setup
	$(PY) -m pytest -q

test-ui: setup
	cd ui && npx tsc --noEmit -p . && npx vitest run

infra-down:
	docker compose down

# Installs only when the lock files change, and never overwrites an existing .env.
setup: .env $(VENV)/.installed ui/node_modules/.installed

.env:
	cp .env.example .env
	@echo "Created .env from .env.example. Check the model settings in it."

$(VENV)/.installed: requirements.txt
	@test -x $(PY) || uv venv --python 3.12 $(VENV)
	uv pip install --python $(PY) -r requirements.txt
	@touch $@

ui/node_modules/.installed: ui/package.json ui/package-lock.json
	npm --prefix ui install
	@touch $@

clean-dev: stop
	rm -rf $(DEV)
