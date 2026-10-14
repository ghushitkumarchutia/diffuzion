UV ?= uv
API := apps/api
WEB := apps/web

.PHONY: install lint typecheck test build check

install:
	cd $(WEB) && npm ci
	$(UV) sync --project $(API) --locked

lint:
	cd $(WEB) && npm run lint
	$(UV) run --project $(API) --locked ruff check $(API)/app $(API)/tests $(API)/alembic evals
	$(UV) run --project $(API) --locked ruff format --check $(API)/app $(API)/tests $(API)/alembic evals

typecheck:
	cd $(WEB) && npm run typecheck
	PYTHONPATH=$(API) $(UV) run --project $(API) --locked mypy --config-file $(API)/pyproject.toml $(API)/app $(API)/alembic

test:
	PYTHONPATH=$(API) $(UV) run --project $(API) --locked pytest $(API)/tests
	cd $(WEB) && npm test

build:
	cd $(WEB) && npm run build

check: lint typecheck test build
