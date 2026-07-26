.PHONY: test lint format docker-build clean

test:
	uv run pytest

lint:
	uv run ruff check .

format:
	uv run ruff format .

docker-build:
	docker build -t hw-genie .

clean:
	rm -rf .ruff_cache/ .pytest_cache/ data/logs/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
