COMPOSE := docker compose --project-directory compose -f compose/docker-compose.yml

.PHONY: data build up up-gateway down logs test lint smoke smoke-gateway clean

data:
	scripts/init-data.sh

build:
	$(COMPOSE) build

up: data
	$(COMPOSE) up -d --build --wait

up-gateway: data
	$(COMPOSE) --profile gateway up -d --build --wait

down:
	$(COMPOSE) --profile gateway down

logs:
	$(COMPOSE) --profile gateway logs -f

test:
	cd wiki-inbox && uv run pytest

lint:
	cd wiki-inbox && uv run ruff check . && uv run ruff format --check . && uv run mypy src

smoke:
	uv run scripts/smoke_test.py

smoke-gateway:
	uv run scripts/smoke_test.py --gateway

clean: down
	rm -rf compose/data
