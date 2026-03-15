import datetime as dt


def test_humanize_seconds(app):
    "Dates less than a minute ago are shown as seconds."
    humanize = app.jinja_env.filters["humanize"]
    past = dt.datetime.utcnow() - dt.timedelta(seconds=30)
    assert humanize(past) == "30s"


def test_humanize_minutes(app):
    "Dates less than an hour ago are shown as minutes."
    humanize = app.jinja_env.filters["humanize"]
    past = dt.datetime.utcnow() - dt.timedelta(minutes=45)
    assert humanize(past) == "45m"


def test_humanize_hours(app):
    "Dates less than a day ago are shown as hours."
    humanize = app.jinja_env.filters["humanize"]
    past = dt.datetime.utcnow() - dt.timedelta(hours=5)
    assert humanize(past) == "5h"


def test_humanize_days(app):
    "Dates less than 8 days ago are shown as days."
    humanize = app.jinja_env.filters["humanize"]
    past = dt.datetime.utcnow() - dt.timedelta(days=4)
    assert humanize(past) == "4d"


def test_humanize_month_day(app):
    "Dates within the current year are shown as month and day."
    humanize = app.jinja_env.filters["humanize"]
    past = dt.datetime.utcnow() - dt.timedelta(days=30)
    assert humanize(past) == past.strftime("%b %d")


def test_humanize_full_date(app):
    "Dates older than a year are shown with the year included."
    humanize = app.jinja_env.filters["humanize"]
    past = dt.datetime.utcnow() - dt.timedelta(days=400)
    assert humanize(past) == past.strftime("%b %d, %Y")
