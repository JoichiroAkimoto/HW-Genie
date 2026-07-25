# Stage 1: Build stage
FROM python:3.13-slim AS builder

WORKDIR /app

# Install build tools for native extensions (libsql-experimental needs a C compiler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 1. Copy only dependency files first to leverage Docker cache
COPY src/python/pyproject.toml src/python/uv.lock* ./

# 2. Export dependencies to requirements.txt and install them system-wide.
#    Using a temporary file avoids shell process-substitution issues.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv export --frozen --no-dev --format requirements-txt > requirements.txt && \
    uv pip install --system -r requirements.txt

# 3. Now copy the source code
COPY src/python/hw_genie ./hw_genie

# 4. Install the project (this will be fast since deps are already installed)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

# Stage 2: Runtime stage
FROM python:3.13-slim

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.13/site-packages /usr/local/lib/python3.13/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy necessary runtime files
COPY src/python/hw_genie ./hw_genie

# Set environment variables
ENV PYTHONUNBUFFERED=1
# Prefer the copied source at /app/hw_genie over the installed site-packages copy
# so that PKG_ROOT (used to resolve ./data relative paths) points at /app.
ENV PYTHONPATH=/app

CMD ["hw-genie", "auth-server"]
