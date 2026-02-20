.PHONY: test setup shell coverage publish-build publish-test publish publish-clean run

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

publish-test:
	uv run hatch publish --repo test

publish:
	uv run hatch publish

publish-clean:
	rm -r dist/

run:
	uv run python -m ssh_auto_forward.cli hetzner
