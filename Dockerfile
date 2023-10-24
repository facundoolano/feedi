FROM python:3.11-alpine

WORKDIR /app

COPY requirements.txt ./

RUN pip install -r requirements.txt

COPY . .

EXPOSE 5000

CMD [ "gunicorn", "-b0.0.0.0:5000", "--env", "FLASK_ENV=development"]
