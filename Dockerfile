# Stage 1: Build stage
FROM python:3.13-slim AS builder

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

# Build the package into a wheel
RUN uv pip install --system .

# Stage 2: Runtime stage
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from builder
# We use --system in builder, so we copy from /usr/local
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy necessary runtime files
COPY src/python/hw_genie ./hw_genie

# Set environment variables
ENV PYTHONUNBUFFERED=1

CMD ["hw-genie", "auth-server"]
