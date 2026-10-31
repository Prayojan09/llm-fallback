FROM python:3.12-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /srv/gateway
RUN groupadd --system gateway && useradd --system --gid gateway --home-dir /srv/gateway gateway
COPY --from=builder /install /usr/local
COPY config ./config
COPY alembic.ini ./alembic.ini
COPY app/db/migrations ./app/db/migrations
USER gateway
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
