# Use official Astral uv image with Python 3.10
FROM ghcr.io/astral-sh/uv:python3.10-bookworm-slim

WORKDIR /app

# Optimize uv performance in container
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV PYTHONUNBUFFERED=1

# Install dependencies first for fast layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# Copy source code and scripts
COPY src/ src/
COPY scripts/ scripts/
COPY config/ config/

# Install the project in the virtualenv
RUN uv sync --frozen

# Ensure virtualenv binaries are on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Default command displays dataset summary
CMD ["python", "scripts/summarize_dataset.py"]
