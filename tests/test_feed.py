"""Regression test for the Friends Listening Now feed."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import create_app, db
from models import ListeningEvent, Song, User
from services import feed_service


def test_listening_now_uses_utc_calendar_day():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    midnight = datetime(2024, 6, 16, tzinfo=timezone.utc)
    with app.app_context():
        viewer, today, boundary, yesterday = [
            User(username=name, email=f"{name}@example.com")
            for name in ("viewer", "today", "boundary", "yesterday")
        ]
        viewer.friends.extend([today, boundary, yesterday])
        db.session.add(viewer)
        db.session.flush()
        viewer_id = viewer.id

        earlier = Song(title="Earlier", artist="Test Artist", shared_by=viewer_id)
        latest = Song(title="Latest", artist="Test Artist", shared_by=viewer_id)
        db.session.add_all([earlier, latest])
        db.session.flush()
        events = [
            (today, earlier, midnight + timedelta(hours=1)),
            (today, latest, midnight + timedelta(hours=2)),
            (boundary, earlier, midnight),
            (yesterday, earlier, midnight - timedelta(seconds=1)),
        ]
        for friend, song, timestamp in events:
            db.session.add(ListeningEvent(user_id=friend.id, song_id=song.id, listened_at=timestamp))
        db.session.commit()

    client = app.test_client()
    with patch.object(feed_service, "datetime") as clock:
        clock.now.return_value = midnight + timedelta(hours=9)
        response = client.get(f"/feed/{viewer_id}/listening-now")

    assert response.status_code == 200
    listening_now = response.get_json()
    entries = [(entry["friend"]["username"], entry["song"]["title"])
               for entry in listening_now["feed"]]
    assert entries == [("today", "Latest"), ("boundary", "Earlier")]

    activity = client.get(f"/feed/{viewer_id}/activity").get_json()
    assert [entry["friend"]["username"] for entry in activity["feed"]] == [
        "today", "today", "boundary", "yesterday"
    ]
