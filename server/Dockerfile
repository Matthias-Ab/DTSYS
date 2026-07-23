FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv

COPY pyproject.toml uv.lock .
RUN pip install --no-cache-dir uv \
    && uv sync --locked --no-install-project

FROM python:3.12-slim

RUN groupadd --system dtsys && useradd --system --gid dtsys --no-create-home --shell /usr/sbin/nologin dtsys

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY . .
RUN chown -R dtsys:dtsys /app

ENV PATH="/opt/venv/bin:$PATH"
USER dtsys

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
