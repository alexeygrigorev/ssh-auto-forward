.PHONY: test setup shell coverage publish-build publish-clean release run docker-build docker-up docker-down

test:
	uv run pytest

setup:
	uv sync --dev

shell:
	uv shell

coverage:
	uv run pytest --cov=ssh_auto_forward --cov-report=term-missing

publish-build:
	uv run hatch build

publish-clean:
	rm -r dist/

# Release: tag the current version and push to trigger CI publish.
release:
	@VERSION=$$(grep -E "^__version__" ssh_auto_forward/__version__.py | sed -E "s/.*['\"]([^'\"]+)['\"].*/\1/"); \
	echo "Releasing $$VERSION"; \
	git tag "$$VERSION"; \
	git push origin "$$VERSION"

run:
	uv run python -m ssh_auto_forward.cli

hetzner:
	uv run python -m ssh_auto_forward.cli hetzner


docker-build:
	docker compose -f docker/docker-compose.yml build

docker-up:
	docker compose -f docker/docker-compose.yml up -d --build

docker-down:
	docker compose -f docker/docker-compose.yml down
