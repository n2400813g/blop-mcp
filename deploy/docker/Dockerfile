FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY src /app/src

RUN pip install --upgrade pip && pip install ".[server]"
RUN playwright install chromium --with-deps --no-shell

# Optional sidecar network service only; MCP stdio stays client-managed.
EXPOSE 8765
CMD ["blop-http"]
