import datetime as dt
import uuid

from feedi.models import db, RssFeed, Entry, User


def test_bucket_single_entry(app):
    "A feed with a single entry is assigned bucket 0 (no frequency can be calculated)."
    fid, uid = make_feed(app)
    add_entries(app, fid, uid, [dt.datetime.utcnow()])
    assert bucket_for(app, fid) == 0


def test_bucket_monthly(app):
    "A feed that posts roughly once a month or less is bucket 0."
    fid, uid = make_feed(app)
    now = dt.datetime.utcnow()
    add_entries(app, fid, uid, [now, now - dt.timedelta(days=61)])
    assert bucket_for(app, fid) == 0


def test_bucket_weekly(app):
    "A feed posting roughly once a week is bucket 1."
    fid, uid = make_feed(app)
    now = dt.datetime.utcnow()
    # 2 entries 15 days apart: ppd = 2/15 ≈ 0.13, which is > 1/30 and <= 1/7
    add_entries(app, fid, uid, [now, now - dt.timedelta(days=15)])
    assert bucket_for(app, fid) == 1


def test_bucket_daily(app):
    "A feed posting roughly once a day is bucket 2."
    fid, uid = make_feed(app)
    now = dt.datetime.utcnow()
    # 7 entries each 7 days apart = 42-day span: ppd = 7/42 ≈ 0.17, which is > 1/7 and <= 1
    add_entries(app, fid, uid, [now - dt.timedelta(days=i * 7) for i in range(7)])
    assert bucket_for(app, fid) == 2


def test_bucket_several_per_day(app):
    "A feed posting a few times per day (up to 5) is bucket 3."
    fid, uid = make_feed(app)
    now = dt.datetime.utcnow()
    add_entries(app, fid, uid, [now - dt.timedelta(hours=i) for i in range(5)])
    assert bucket_for(app, fid) == 3


def test_bucket_high_frequency(app):
    "A feed posting up to 20 times per day is bucket 4."
    fid, uid = make_feed(app)
    now = dt.datetime.utcnow()
    add_entries(app, fid, uid, [now - dt.timedelta(minutes=i) for i in range(20)])
    assert bucket_for(app, fid) == 4


def test_bucket_very_high_frequency(app):
    "A feed posting more than 20 times per day is bucket 5."
    fid, uid = make_feed(app)
    now = dt.datetime.utcnow()
    add_entries(app, fid, uid, [now - dt.timedelta(minutes=i) for i in range(21)])
    assert bucket_for(app, fid) == 5


def test_bucket_ignores_old_entries(app):
    "Entries older than the retention window are not counted when calculating the bucket."
    fid, uid = make_feed(app)
    with app.app_context():
        retention = app.config["DELETE_AFTER_DAYS"]
    now = dt.datetime.utcnow()
    # 21 entries well outside the retention window — would push bucket to 4+ if counted
    old_entries = [now - dt.timedelta(days=retention + 10 + i) for i in range(21)]
    # only 1 entry within the retention window
    add_entries(app, fid, uid, old_entries + [now])
    assert bucket_for(app, fid) == 0


def make_feed(app):
    "Create a user and a fresh feed, return (feed_id, user_id)."
    with app.app_context():
        user = User(email=f"u-{uuid.uuid4()}@test.com")
        user.set_password("x")
        db.session.add(user)
        db.session.flush()
        feed = RssFeed(name=f"f-{uuid.uuid4()}", url="http://x.com/feed", user_id=user.id)
        db.session.add(feed)
        db.session.commit()
        return feed.id, user.id


def add_entries(app, feed_id, user_id, dates):
    with app.app_context():
        for d in dates:
            entry = Entry(
                feed_id=feed_id,
                user_id=user_id,
                remote_id=str(uuid.uuid4()),
                title="t",
                display_date=d,
                sort_date=d,
            )
            db.session.add(entry)
        db.session.commit()


def bucket_for(app, feed_id):
    with app.app_context():
        feed = db.session.get(RssFeed, feed_id)
        return feed.calculate_bucket()
