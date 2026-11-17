# Mixtape Bug Hunt

## AI Usage

I used Codex to explain the routes and services, walk through `get_playlist()`, and trace how a listening event becomes a feed entry. I also used it to investigate the streak and feed reports and to help implement, test, and document the fixes. The initial feed investigation suggested a 30-minute window based on the sample data. The project brief says to include listeners from today, so the final check uses fixed timestamps on both sides of UTC midnight. I used the repository's pytest suite and Flask requests against an isolated SQLite database to check the diagnoses and fixes.

## Codebase Map

Mixtape is a Flask JSON API for a social music app. Its main layers are HTTP routes, feature services, and SQLAlchemy models. A request generally follows this path: route parses input → service queries or updates models through `db.session` → route formats the result as JSON.

**Application setup — `app.py`.** The module creates the shared SQLAlchemy object, `db`. Its `create_app(config=None)` factory configures the database and secret key, accepts configuration overrides, initializes SQLAlchemy, and registers four Blueprints under `/songs`, `/playlists`, `/users`, and `/feed`. It calls `db.create_all()` inside an application context. The default database is SQLite (`sqlite:///mixtape.db`), and `DATABASE_URL` can select a different database. Running the module directly starts Flask's development server.

**Database structure — `models.py`.** The seven SQLAlchemy models use UUID strings as primary keys:

- `User` stores identity information, the listening streak, and the last listening timestamp. Its relationships connect users to friends, shared songs, ratings, listening events, notifications, and playlists.
- `Tag` stores a unique tag name.
- `Song` stores title, artist, album, genre, sharing information, and a reference to the user who shared it. It relates to tags, ratings, and listening events.
- `ListeningEvent` records which user listened to which song and when. Feed queries use these records.
- `Rating` connects a user to a song with a score and timestamp. A database uniqueness constraint allows one rating row per user/song pair.
- `Playlist` stores a name, creator, creation timestamp, and collaboration flag, and relates to its songs.
- `Notification` stores a recipient, notification type, message body, creation timestamp, and read status.

Three association tables represent many-to-many relationships:

- `friendships` connects a user ID to a friend ID. The seed script inserts both directions for each friendship.
- `song_tags` connects songs to tags.
- `playlist_entries` connects playlists to songs and also records `position`, `added_by`, and `added_at`. Playlist order comes from the explicit `position` column.

The API-facing models define `to_dict()` methods that select response fields and convert timestamps to ISO strings. `Song.to_dict()` includes tag names; `Playlist.to_dict()` returns playlist metadata separately from its song contents.

**HTTP interface — `routes/`.** Each route module defines a Flask Blueprint. Handlers read path parameters, query parameters, or JSON bodies, check required inputs, call feature functions, and use `jsonify()` and HTTP status codes to return results. They also translate caught `ValueError` exceptions into JSON error responses.

- `routes/songs.py` handles song search, song details, ratings, and listening events. `GET /songs/search?q=...` calls `search_service.search_songs()`, and `GET /songs/<song_id>` calls `search_service.get_song()`. `POST /songs/<song_id>/rate` calls `notification_service.rate_song()`. `POST /songs/<song_id>/listen` calls `streak_service.record_listening_event()`.
- `routes/playlists.py` handles playlist creation, metadata, and contents. `POST /playlists/` calls `playlist_service.create_playlist()`. `GET /playlists/<playlist_id>` calls `get_playlist()`, and `GET /playlists/<playlist_id>/songs` calls `get_playlist_songs()`. Adding a song with `POST /playlists/<playlist_id>/songs` delegates to `notification_service.add_to_playlist()`.
- `routes/users.py` handles profiles, streaks, and notifications. `GET /users/<user_id>` retrieves the `User` directly through `db.session.get()`. The streak endpoint calls `streak_service.get_streak()`. Notification endpoints call `notification_service.get_notifications()` and `mark_as_read()`, with an optional `unread_only=true` query parameter for retrieval.
- `routes/feed.py` exposes `GET /feed/<user_id>/listening-now` and `GET /feed/<user_id>/activity`. Their handlers call `feed_service.get_friends_listening_now()` and `get_activity_feed()`, respectively, and return a `feed` list plus its `count`.

**Feature logic — `services/`.** Services operate on models using the shared database session. They return model objects or dictionaries, while the routes own HTTP response formatting.

- `search_service.py`: `search_songs()` performs case-insensitive substring matching against song titles and artists, using a query that outer-joins `song_tags`. Results are serialized through `Song.to_dict()`. `get_song()` looks up a song by primary key and returns its dictionary.
- `playlist_service.py`: `create_playlist()` looks up the creator and saves a new `Playlist`. `get_playlist()` retrieves metadata. `get_playlist_songs()` joins `Song` to `playlist_entries`, filters by playlist ID, and orders the query by ascending position. `get_user_playlists()` retrieves playlists by creator ID.
- `streak_service.py`: `record_listening_event()` creates a `ListeningEvent`, calls `update_listening_streak()`, and commits the event and user changes. Streak calculations compare UTC calendar dates with the user's last listening date. `get_streak()` retrieves the stored streak value.
- `feed_service.py`: `get_friends_listening_now()` gets the viewer's friends, applies the `RECENT_THRESHOLD` cutoff to their listening events, sorts newest first, and keeps the latest event per friend. `get_activity_feed()` retrieves friends' listening events newest first with a default limit of 20. Both construct entries containing a friend dictionary, a song dictionary, and a listening timestamp.
- `notification_service.py`: `create_notification()` saves a notification; `get_notifications()` retrieves notifications newest first with optional unread filtering; `mark_as_read()` updates read status. This module also coordinates `add_to_playlist()`, including notifying the original song sharer when another user adds the song, and `rate_song()`, which validates and saves ratings.

**Data flow — rating a song.** A client sends `POST /songs/<song_id>/rate` with `user_id` and `score`. The `rate()` handler in `routes/songs.py` reads the body, checks the required fields, converts the score to an integer, and calls `notification_service.rate_song()`. That service validates the score is between 1 and 5, looks up the `Song` and `User`, and searches for an existing `Rating` for that user/song pair. It updates the existing score or creates a new rating, commits the transaction, and returns the `Rating` object. The route calls `rating.to_dict()` and returns JSON with status 201.

**Data flow — a listen appearing in a friend's feed.** A client sends `POST /songs/<song_id>/listen` with the listener's `user_id`. The `listen()` route calls `streak_service.record_listening_event()`, which looks up the user, creates and stages a timestamped `ListeningEvent`, calls `update_listening_streak()`, and commits. The route serializes the returned event and responds with status 201. Later, a friend requests `GET /feed/<viewer_id>/listening-now`. The `listening_now()` route calls `feed_service.get_friends_listening_now()`, which queries saved events for the viewer's friends, filters by recency, and selects each friend's latest event. It loads the related users and songs, converts them to dictionaries, and returns the entries for the route's JSON response. The two requests connect through persisted `ListeningEvent` records; the feed is assembled when read.

**Supporting files.** `seed_data.py` uses `create_app()` and the same models to rebuild the database and populate users, friendships, tags, songs, playlists, listening history, and notifications. `tests/test_search.py`, `tests/test_playlists.py`, and `tests/test_streaks.py` exercise service functions with pytest fixtures and an in-memory SQLite database. The fixtures use application-factory configuration overrides to isolate test data. `requirements.txt` declares Flask, Flask-SQLAlchemy, SQLAlchemy, python-dotenv, and pytest. `README.md` documents the project structure, setup, and execution commands.

**Architectural pattern.** Route groups and service boundaries follow different responsibilities: song routes use search, notification, and streak services, while playlist routes use playlist and notification services. Most handlers delegate feature work to services; the profile handler performs its lookup directly. Services share model definitions and `db.session`, and their write operations use `commit()` to persist changes. Model serialization connects database objects to the JSON API.

## Root Cause Analyses

**Issue #1 — My listening streak keeps resetting**

- **How you reproduced it:** On the starter revision, used a user with a streak of 12 and a Saturday, June 15, 2024 UTC listen. With the clock set to Sunday morning, sent `POST /songs/<song_id>/listen`, then called `GET /users/<user_id>/streak`. The listen succeeded, but the streak was 1 instead of 13. The existing Sunday pytest case also reproduced the reset with a streak starting at 1.
- **How you found the root cause:** Traced `routes/songs.py:listen()` to `record_listening_event()` in `services/streak_service.py`, then to `update_listening_streak()`. For the Saturday-to-Sunday dates, the date subtraction gave one day, but the weekday check evaluated to false and sent execution to the reset branch.
- **The root cause:** The increment branch required both `days_since_last == 1` and `today.weekday() != 6`. Sunday is weekday 6, so a valid consecutive day fell through to the same branch used for a skipped day. The service committed the reset value along with the listening event.
- **Your fix:** Use `days_since_last == 1` on its own so any consecutive UTC calendar day increments the streak.
- **Your side-effect check:** Ran `tests/test_streaks.py`: all five cases passed, covering first listen, an ordinary consecutive day, Sunday, a repeated listen on the same day, and a skipped day.
