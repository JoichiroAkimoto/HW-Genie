.PHONY: test test-cov lint format docker-build clean clean-logs

test:
	uv run pytest

test-cov:
	uv run pytest --cov=hw_genie --cov-report=term-missing --cov-report=xml

lint:
	uv run ruff check .

format:
	uv run ruff format .

docker-build:
	docker build -t hw-genie .

clean:
	rm -rf .ruff_cache/ .pytest_cache/
	find . -not -path './.venv/*' -not -path './node_modules/*' -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

clean-logs:
	rm -rf data/logs/
