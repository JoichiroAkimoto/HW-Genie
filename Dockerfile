FROM python:3.13-slim

WORKDIR /app

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY src/python/pyproject.toml src/python/uv.lock* ./
RUN uv pip install --system .

COPY src/python/hw_genie ./hw_genie

CMD ["hw-genie", "auth-server"]
