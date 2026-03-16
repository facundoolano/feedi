FROM python:3.11-alpine

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install python dependencies
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-cache

COPY . .

EXPOSE 9988

# run in dev by default, override with docker run -e FLASK_ENV=production
ENV FLASK_ENV=development

CMD ["sh", "-c", "uv run gunicorn -b 0.0.0.0:9988 --env FLASK_ENV=${FLASK_ENV}"]
