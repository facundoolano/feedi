FROM node:20-alpine AS node

FROM python:3.11-alpine

# Copy node to python-alpine image
COPY --from=node /usr/lib /usr/lib
COPY --from=node /usr/local/lib /usr/local/lib
COPY --from=node /usr/local/include /usr/local/include
COPY --from=node /usr/local/bin /usr/local/bin

WORKDIR /app

# Install python dependencies
COPY requirements.txt ./

RUN pip install -r requirements.txt --no-cache-dir

# Install node dependencies
# Copy both package.json and package-lock.json
COPY package*.json ./

RUN npm ci --omit=dev

COPY . .

EXPOSE 9988

CMD [ "gunicorn", "-b0.0.0.0:9988", "--env", "FLASK_ENV=development"]
