# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
FROM python:3.13-slim-bookworm@sha256:5024f48ba9441d4b13a95d3945abc6365538e3a31109833367a1923523c6efed AS runtime

ARG APP_UID=10001
ARG APP_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    WIZ_HATCH_DATA_PATH=/app/data/pets.json \
    HEALTH_HOST=0.0.0.0 \
    HEALTH_PORT=8080 \
    PATH=/opt/venv/bin:$PATH

RUN groupadd --gid "${APP_GID}" app \
    && useradd --uid "${APP_UID}" --gid "${APP_GID}" \
        --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
    && python -m venv /opt/venv

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir --requirement requirements.txt

COPY --chown=${APP_UID}:${APP_GID} src ./src
COPY --chown=${APP_UID}:${APP_GID} data ./data

USER 10001:10001
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"]

CMD ["python", "-m", "wiz_hatch.bot"]
