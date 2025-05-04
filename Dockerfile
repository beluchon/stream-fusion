FROM python:3.12-alpine AS development

ENV PYTHONUNBUFFERED=1 \
    POETRY_VERSION=1.8.3

RUN apk add --no-cache \
        gcc \
        musl-dev \
        libffi-dev \
        openssl \
        openssh \
        postgresql-client \
        git \
    && pip install --no-cache-dir "poetry==$POETRY_VERSION"

WORKDIR /app

# Copier d'abord les fichiers de dépendances
COPY pyproject.toml poetry.lock ./

# Installer les dépendances
RUN poetry config virtualenvs.create false \
    && poetry install --no-root --with dev

# Copier le reste du code source
COPY . .

# S'assurer que le répertoire static/videos existe
RUN mkdir -p /app/stream_fusion/static/videos

# Copier explicitement les fichiers vidéo
COPY stream_fusion/static/videos/*.mp4 /app/stream_fusion/static/videos/

ARG GUNICORN_PORT=8080
ENV EXPOSE_PORT=${GUNICORN_PORT}
EXPOSE ${EXPOSE_PORT}

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
