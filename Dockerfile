FROM node:20-alpine AS node

FROM python:3.11-alpine

# Copy node to python-alpine image
COPY --from=node /usr/lib /usr/lib
COPY --from=node /usr/local/lib /usr/local/lib
COPY --from=node /usr/local/include /usr/local/include
COPY --from=node /usr/local/bin /usr/local/bin

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install python dependencies
COPY pyproject.toml uv.lock ./

RUN uv sync --frozen --no-dev --no-cache

# Install node dependencies
COPY package*.json ./

RUN npm ci --omit=dev

COPY . .

EXPOSE 9988

# run in dev by default, override with docker run -e FLASK_ENV=production
ENV FLASK_ENV=development

CMD ["sh", "-c", "uv run gunicorn -b 0.0.0.0:9988 --env FLASK_ENV=${FLASK_ENV}"]
