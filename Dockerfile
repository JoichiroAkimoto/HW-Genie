FROM python:3.13-slim

WORKDIR /app

# Install build tools for native extensions (required for libsql-experimental)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY src/python/pyproject.toml src/python/uv.lock* ./
COPY src/python/hw_genie ./hw_genie

# Install the package
RUN uv pip install --system .

CMD ["hw-genie", "auth-server"]
