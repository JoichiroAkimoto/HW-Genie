.PHONY: test lint format typecheck docker-build clean

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

typecheck:
	uv run ruff check .

docker-build:
	docker build -t hw-genie .

clean:
	rm -rf .ruff_cache/ .pytest_cache/ data/logs/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
