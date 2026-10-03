"""Kviss - a Jeopardy-style quiz for one host screen (phone mirrored to a TV).

Quizzes (title, categories, questions) are stored in a SQLite database and can be
played again and again with different players. Quizzes are added as JSON through
the API (/api/quizzes) or the `import` command. The host picks a quiz and types
the players on /nytt, which starts a game.

Each game keeps a frozen copy of the quiz it was started with, so editing or
deleting a quiz never changes a running game or the results of earlier ones.
Game progress is saved to the database after every action, so a reloaded phone
browser or a restarted server picks up where the game left off.
"""

import copy
import json
import mimetypes
import os
import secrets
import sqlite3
import sys
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import (Flask, Response, abort, jsonify, redirect, render_template, request, send_from_directory,
                   url_for)

from schemas import (MIN_WAGER, AdjustForm, ConfigError, ConfirmForm, FormError, JudgeForm, NewGameForm, UndoForm,
                     WagerForm, parse_form, parse_quiz)

BASE_DIR = Path(__file__).resolve().parent
mimetypes.add_type("application/manifest+json", ".webmanifest")
UNDO_LIMIT = 50
MAX_UPLOAD = 1024 * 1024  # bytes; a big quiz is well under 100 kB


def _env_path(name, default):
    return Path(os.environ.get(name) or default)


def default_db():
    return _env_path("KVISS_DB", BASE_DIR / "kviss.db")


def default_media():
    return _env_path("KVISS_MEDIA", BASE_DIR / "media")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --- input ------------------------------------------------------------------
# The rules for quiz JSON and form input live in schemas.py.


def validate_quiz(data, media_dir):
    """Check a parsed quiz and return a cleaned copy. Raises ConfigError with readable messages.

    Audio files for music questions must already be in media_dir.
    """
    return parse_quiz(data, media_dir)


def load_quiz(path, media_dir=None):
    """Read and validate a quiz file. Audio files are looked up in media_dir,
    by default the 'media' folder next to the quiz file."""
    path = Path(path)
    try:
        data = json.loads(path.read_bytes())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: invalid JSON: {e}") from e
    return validate_quiz(data, media_dir or path.parent / "media")


# --- storage ------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS quizzes (
    id         INTEGER PRIMARY KEY,
    slug       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    data       TEXT NOT NULL,           -- the validated quiz as JSON
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS games (
    id         INTEGER PRIMARY KEY,
    quiz_id    INTEGER REFERENCES quizzes(id) ON DELETE SET NULL,
    quiz       TEXT NOT NULL,           -- frozen copy of the quiz as it was when the game started
    name       TEXT,                    -- set by the host when starting; NULL = named after the quiz
    players    TEXT NOT NULL,           -- JSON list of names
    state      TEXT NOT NULL,           -- JSON: scores, used, wrong, Daily Doubles, undo history
    started_at TEXT NOT NULL,
    ended_at   TEXT                     -- NULL for the game on the TV right now
);
-- At most one current game.
CREATE UNIQUE INDEX IF NOT EXISTS one_current_game ON games ((ended_at IS NULL)) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS games_by_quiz ON games (quiz_id);
PRAGMA user_version = 2;
"""


def fresh_state(n_players, daily_doubles=()):
    return {
        "scores": [0] * n_players,
        "used": {},     # "c-r" -> index of player who answered correctly, or None
        "wrong": {},    # "c-r" -> [indexes of players who answered wrong]
        "daily_doubles": list(daily_doubles),  # ["c-r", ...], hidden until the tile is opened
        "wagers": {},   # "c-r" -> {"player": index, "amount": points} for Daily Doubles
        "history": [],  # snapshots for undo
    }


def pick_daily_doubles(quiz, count):
    """Hide Daily Doubles on `count` random tiles, as "c-r" keys. Like on the TV show, no two
    share a category unless there are more Daily Doubles than categories.

    Uses the system's random source, so nobody (not even the host) can work out where they are.
    """
    rng = secrets.SystemRandom()
    tiles = [(c, r) for c, cat in enumerate(quiz["categories"]) for r in range(len(cat["questions"]))]
    rng.shuffle(tiles)
    picked, categories = [], set()
    for c, r in tiles:  # one per category first ...
        if len(picked) < count and c not in categories:
            picked.append((c, r))
            categories.add(c)
    for tile in tiles:  # ... then fill up if there are more Daily Doubles than categories
        if len(picked) < count and tile not in picked:
            picked.append(tile)
    return [f"{c}-{r}" for c, r in sorted(picked)]


class Store:
    """The SQLite database. Every call opens its own short-lived connection, which
    is cheap for SQLite and keeps it safe across gunicorn's threads."""

    def __init__(self, path):
        self.path = str(path)
        with self._db() as db:
            db.execute("PRAGMA journal_mode = WAL")  # readers (e.g. the sqlite3 shell) never block a save
            db.executescript(SCHEMA)
            # Version 1 databases have no games.name; CREATE TABLE IF NOT EXISTS doesn't add it.
            if "name" not in {col["name"] for col in db.execute("PRAGMA table_info(games)")}:
                db.execute("ALTER TABLE games ADD COLUMN name TEXT")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            with db:  # one transaction: commit on success, roll back on error
                yield db
        finally:
            db.close()

    # --- quizzes -----------------------------------------------------------

    def save_quiz(self, quiz):
        """Insert, or replace the quiz with the same slug. Returns True if it is new."""
        data = {k: v for k, v in quiz.items() if k != "slug"}
        stamp = now()
        with self._db() as db:
            new = db.execute("SELECT 1 FROM quizzes WHERE slug = ?", (quiz["slug"],)).fetchone() is None
            db.execute(
                "INSERT INTO quizzes (slug, title, data, created_at, updated_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (slug) DO UPDATE SET title = excluded.title, data = excluded.data, "
                "updated_at = excluded.updated_at",
                (quiz["slug"], quiz["title"], json.dumps(data, ensure_ascii=False), stamp, stamp))
        return new

    _QUIZZES = ("SELECT q.*, COUNT(g.id) AS plays, MAX(g.started_at) AS last_played "
                "FROM quizzes q LEFT JOIN games g ON g.quiz_id = q.id {where} "
                "GROUP BY q.id ORDER BY q.title COLLATE NOCASE")

    def quizzes(self):
        with self._db() as db:
            rows = db.execute(self._QUIZZES.format(where="")).fetchall()
        return [self._quiz(r) for r in rows]

    def quiz(self, slug):
        with self._db() as db:
            row = db.execute(self._QUIZZES.format(where="WHERE q.slug = ?"), (slug,)).fetchone()
        return self._quiz(row) if row else None

    @staticmethod
    def _quiz(row):
        data = json.loads(row["data"])
        return {
            "slug": row["slug"], **data,
            "questions": sum(len(c["questions"]) for c in data["categories"]),
            "music": sum(1 for c in data["categories"] for q in c["questions"] if q.get("youtube") or q.get("audio")),
            "updated_at": row["updated_at"], "plays": row["plays"], "last_played": row["last_played"],
        }

    def delete_quiz(self, slug):
        """Earlier games keep their own copy of the quiz, so they survive this."""
        with self._db() as db:
            return db.execute("DELETE FROM quizzes WHERE slug = ?", (slug,)).rowcount > 0

    def is_empty(self):
        with self._db() as db:
            return db.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0] == 0

    # --- games -------------------------------------------------------------

    def start_game(self, slug, players, name=None, daily_doubles=0):
        """End the current game and start a new one, with `daily_doubles` Daily Doubles hidden
        on the board. Returns the new game's row, or None if there is no such quiz. A current
        game where nothing was scored is dropped instead of being kept in the history."""
        with self._db() as db:
            quiz = db.execute("SELECT id, data FROM quizzes WHERE slug = ?", (slug,)).fetchone()
            if quiz is None:
                return None
            current = db.execute("SELECT id, state FROM games WHERE ended_at IS NULL").fetchone()
            if current and not json.loads(current["state"])["history"]:
                db.execute("DELETE FROM games WHERE id = ?", (current["id"],))
            elif current:
                db.execute("UPDATE games SET ended_at = ? WHERE id = ?", (now(), current["id"]))
            state = fresh_state(len(players), pick_daily_doubles(json.loads(quiz["data"]), daily_doubles))
            game_id = db.execute(
                "INSERT INTO games (quiz_id, quiz, name, players, state, started_at) VALUES (?, ?, ?, ?, ?, ?)",
                (quiz["id"], quiz["data"], name or None, json.dumps(players, ensure_ascii=False),
                 json.dumps(state), now())).lastrowid
            return db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()

    def current_game(self):
        with self._db() as db:
            return db.execute("SELECT * FROM games WHERE ended_at IS NULL").fetchone()

    def games(self, limit=20, ended=False):
        """The newest games first. ended=True leaves out the current game."""
        where = "WHERE ended_at IS NOT NULL" if ended else ""
        with self._db() as db:
            return db.execute(f"SELECT * FROM games {where} ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def game(self, game_id):
        with self._db() as db:
            return db.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()

    def save_state(self, game_id, state):
        with self._db() as db:
            db.execute("UPDATE games SET state = ? WHERE id = ?", (json.dumps(state), game_id))

    def last_players(self):
        with self._db() as db:
            row = db.execute("SELECT players FROM games ORDER BY id DESC LIMIT 1").fetchone()
        return json.loads(row["players"]) if row else []


# --- game ---------------------------------------------------------------------


class Game:
    """One game: a frozen quiz, its players and their progress. Guarded by a lock
    and saved after every change."""

    def __init__(self, row, store=None, version=None):
        self.id = row["id"]
        self.store = store
        # Templates read the players from quiz.players, as when they lived in the quiz file.
        self.quiz = {**json.loads(row["quiz"]), "players": json.loads(row["players"])}
        self.name = row["name"] or self.quiz["title"]  # games from before names existed: the quiz title
        self.state = {"daily_doubles": [], "wagers": {}, **json.loads(row["state"])}  # older games have neither
        self.started_at, self.ended_at = row["started_at"], row["ended_at"]
        self.audio_files = {q["audio"] for cat in self.quiz["categories"]  # the only files /media/ serves
                            for q in cat["questions"] if q.get("audio")}
        self.lock = threading.Lock()
        # Live, in-memory only (not saved, not undoable): the question the TV is
        # showing, and a counter that changes whenever anything a viewer sees changes.
        self.current = None
        self.version = secrets.randbelow(1 << 30) if version is None else version

    # --- persistence -------------------------------------------------------

    def _save(self):
        self.store.save_state(self.id, self.state)
        self.version += 1

    def _checkpoint(self):
        snap = {k: copy.deepcopy(self.state[k]) for k in ("scores", "used", "wrong", "wagers")}
        self.state["history"] = (self.state["history"] + [snap])[-UNDO_LIMIT:]

    # --- queries -----------------------------------------------------------

    def question(self, c, r):
        cats = self.quiz["categories"]
        if not (0 <= c < len(cats) and 0 <= r < len(cats[c]["questions"])):
            return None
        return cats[c]["questions"][r]

    def is_used(self, c, r):
        return f"{c}-{r}" in self.state["used"]

    def wrong_players(self, c, r):
        return self.state["wrong"].get(f"{c}-{r}", [])

    def winner(self, c, r):
        return self.state["used"].get(f"{c}-{r}")

    def total_questions(self):
        return sum(len(cat["questions"]) for cat in self.quiz["categories"])

    def is_over(self):
        return len(self.state["used"]) >= self.total_questions()

    def in_progress(self):
        """Something was scored and the board isn't finished: starting another game would cut it short."""
        return bool(self.state["history"]) and not self.is_over()

    def can_undo(self):
        return bool(self.state["history"])

    # Daily Doubles

    def is_daily_double(self, c, r):
        return f"{c}-{r}" in self.state["daily_doubles"]

    def daily_double_shown(self, c, r):
        """A Daily Double everyone has seen: opened and bet on, played, or left when the game ended.
        Anything that lists tiles uses this, so the others stay a surprise."""
        return self.is_daily_double(c, r) and (self.wager(c, r) is not None or self.is_used(c, r)
                                                or bool(self.ended_at))

    def wager(self, c, r):
        """The bet on a Daily Double, {"player": index, "amount": points}, or None before the bet."""
        return self.state["wagers"].get(f"{c}-{r}")

    def top_value(self):
        return max(q["value"] for cat in self.quiz["categories"] for q in cat["questions"])

    def max_wager(self, player):
        """Your whole score, or the highest value on the board if that is more (as on the TV show)."""
        return max(self.state["scores"][player], self.top_value())

    def standings(self):
        players = [{"name": n, "score": s} for n, s in zip(self.quiz["players"], self.state["scores"])]
        players.sort(key=lambda p: p["score"], reverse=True)
        # Tied scores share a place, and the next place is skipped (1, 1, 3).
        for i, p in enumerate(players):
            tied = i > 0 and p["score"] == players[i - 1]["score"]
            p["place"] = players[i - 1]["place"] if tied else i + 1
        return players

    # --- actions -----------------------------------------------------------

    def set_current(self, c=None, r=None, test=False):
        """Remember which question the TV shows (None = the board), for the host view.
        test: opened from the admin page's music check, which doesn't reveal Daily Doubles."""
        current = None if c is None else (c, r, test)
        with self.lock:
            if current != self.current:
                self.current = current
                self.version += 1

    def place_wager(self, c, r, player, amount):
        """Bet on a Daily Double. Returns None, or a message for the host if the bet isn't allowed."""
        key = f"{c}-{r}"
        with self.lock:
            if not self.is_daily_double(c, r) or key in self.state["used"] or key in self.state["wagers"]:
                return None  # nothing to bet on (any more): the question page shows what happened
            if not 0 <= player < len(self.state["scores"]):
                return "Velg hvem som fant Dagens dobbel."
            top = self.max_wager(player)
            if not MIN_WAGER <= amount <= top:
                return f"{self.quiz['players'][player]} kan satse fra {MIN_WAGER} til {top}."
            self._checkpoint()
            self.state["wagers"][key] = {"player": player, "amount": amount}
            self._save()
            return None

    def judge(self, c, r, player, result):
        """Apply a host decision. Returns True if the question is now finished."""
        q = self.question(c, r)
        key = f"{c}-{r}"
        with self.lock:
            if key in self.state["used"]:
                return True
            if self.is_daily_double(c, r):
                return self._judge_daily_double(key, player, result)
            if result == "nobody":
                self._checkpoint()
                self.state["used"][key] = None
            elif result in ("correct", "wrong"):
                if not (0 <= player < len(self.state["scores"])) or player in self.wrong_players(c, r):
                    return False
                self._checkpoint()
                if result == "correct":
                    self.state["scores"][player] += q["value"]
                    self.state["used"][key] = player
                else:
                    self.state["scores"][player] -= q["value"]
                    self.state["wrong"].setdefault(key, []).append(player)
            else:
                return False
            self._save()
            return key in self.state["used"]

    def _judge_daily_double(self, key, player, result):
        """Only the player who bet answers, once: right wins the bet, wrong loses it. Called with the lock held."""
        wager = self.state["wagers"].get(key)
        if wager is None or player != wager["player"] or result not in ("correct", "wrong"):
            return False
        self._checkpoint()
        if result == "correct":
            self.state["scores"][player] += wager["amount"]
            self.state["used"][key] = player
        else:
            self.state["scores"][player] -= wager["amount"]
            self.state["wrong"][key] = [player]
            self.state["used"][key] = None
        self._save()
        return True

    def adjust(self, player, delta):
        with self.lock:
            if not (0 <= player < len(self.state["scores"])) or delta == 0:
                return
            self._checkpoint()
            self.state["scores"][player] += delta
            self._save()

    def undo(self):
        with self.lock:
            if not self.state["history"]:
                return
            self.state.update(self.state["history"].pop())
            self._save()

    def reset(self):
        """Start the board over. The Daily Doubles move, since the room has seen where some of them were."""
        with self.lock:
            hidden = pick_daily_doubles(self.quiz, len(self.state["daily_doubles"]))
            self.state = fresh_state(len(self.quiz["players"]), hidden)
            self._save()


class Kviss:
    """The quiz library, and the one game that is on the TV right now (or None)."""

    def __init__(self, store, media_dir):
        self.store = store
        self.media_dir = Path(media_dir)
        self.lock = threading.Lock()
        row = store.current_game()
        self.game = Game(row, store) if row else None

    def start(self, slug, players, name=None, daily_doubles=0):
        with self.lock:
            row = self.store.start_game(slug, players, name, daily_doubles)
            if row is None:
                return None
            # Continue the version count so the host view always notices the switch.
            old = self.game
            self.game = Game(row, self.store, version=old.version + 1 if old else None)
            return self.game


# --- web app ------------------------------------------------------------------


def create_app(db_path=None, password=None, media_dir=None, seed=None):
    """seed: quiz files to import when the database has no quizzes yet (first start)."""
    db_path = db_path or default_db()
    media_dir = Path(media_dir or default_media())
    password = password if password is not None else os.environ.get("KVISS_PASSWORD", "")
    if seed is None:
        seed = [_env_path("KVISS_CONFIG", BASE_DIR / "quiz.json"), BASE_DIR / "quiz-example.json"]

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD  # larger request bodies get 413 before they are read
    tz_name = os.environ.get("KVISS_TZ") or "Europe/Oslo"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        app.logger.warning("Unknown time zone KVISS_TZ=%r, showing dates in UTC", tz_name)
        tz = timezone.utc
    store = Store(db_path)
    if store.is_empty():
        for path in seed:
            if Path(path).is_file():
                try:
                    store.save_quiz(load_quiz(path, media_dir))
                except ConfigError as e:
                    app.logger.warning("Not importing %s: %s", path, e)
    kviss = Kviss(store, media_dir)
    app.config["KVISS"] = kviss

    @app.before_request
    def guard():
        if password:
            auth = request.authorization
            given = (auth.password or "") if auth else ""
            if not secrets.compare_digest(given.encode(), password.encode()):
                return Response("Innlogging kreves", 401, {"WWW-Authenticate": 'Basic realm="kviss"'})
        # Reject cross-site form posts (CSRF): browsers send Origin on POST.
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403)

    @app.template_filter("mmss")
    def mmss(seconds):
        seconds = int(seconds)
        return f"{seconds // 60}:{seconds % 60:02d}"

    @app.template_filter("when")
    def when(stamp):
        return datetime.fromisoformat(stamp).astimezone(tz).strftime("%d.%m.%Y %H:%M") if stamp else ""

    @app.context_processor
    def inject():
        game = kviss.game
        return {"game": game, "quiz": game.quiz if game else {"title": "Kviss"},
                "game_name": game.name if game else "Kviss"}

    def form(model):
        """The posted form, validated by a schemas.py model. Bad values (only possible with a
        hand-made request, the pages never send them) get a 400."""
        try:
            return parse_form(model, request.form.to_dict())
        except FormError:
            abort(400)

    def current_game():
        """The game on the TV, or abort with a redirect to the landing page."""
        game = kviss.game
        if game is None:
            abort(redirect(url_for("home")))
        return game

    # --- landing page and results ------------------------------------------

    @app.get("/")
    def home():
        past = [Game(row) for row in store.games(limit=50, ended=True)]
        return render_template("home.html", past=past)

    @app.get("/resultat/<int:game_id>")
    def results(game_id):
        game = kviss.game
        if game is None or game.id != game_id:  # the current game is read live, the rest from the database
            row = store.game(game_id)
            if row is None:
                abort(404)
            game = Game(row)
        rows = max(len(cat["questions"]) for cat in game.quiz["categories"])
        return render_template("results.html", g=game, rows=rows)

    # --- the game on the TV --------------------------------------------------

    @app.get("/brett")
    def board():
        game = current_game()
        game.set_current(None)
        if game.is_over():
            return render_template("final.html", ranking=game.standings())
        cats = game.quiz["categories"]
        rows = max(len(cat["questions"]) for cat in cats)
        return render_template("board.html", rows=rows)

    @app.get("/q/<int:c>/<int:r>")
    def question(c, r):
        game = current_game()
        q = game.question(c, r)
        if q is None:
            abort(404)
        # ?test=1: the admin page's music check. It shows the question without judge buttons
        # and without giving away a Daily Double.
        test = request.args.get("test") == "1"
        game.set_current(c, r, test)
        category = game.quiz["categories"][c]["name"]
        if game.is_daily_double(c, r) and game.wager(c, r) is None and not game.is_used(c, r) and not test:
            return render_template("daily_double.html", c=c, r=r, q=q, category=category, min_wager=MIN_WAGER)
        reveal = request.args.get("reveal") == "1" or game.is_used(c, r)
        return render_template("question.html", c=c, r=r, q=q, category=category, reveal=reveal, test=test)

    @app.post("/q/<int:c>/<int:r>/wager")
    def wager(c, r):
        game = current_game()
        q = game.question(c, r)
        if q is None:
            abort(404)
        try:
            bet = parse_form(WagerForm, request.form.to_dict())
            error = game.place_wager(c, r, bet.player, bet.amount)
        except FormError as e:
            error = str(e)
        if error:
            return render_template("daily_double.html", c=c, r=r, q=q, error=error, min_wager=MIN_WAGER,
                                   category=game.quiz["categories"][c]["name"], form=request.form), 400
        return redirect(url_for("question", c=c, r=r))

    @app.post("/q/<int:c>/<int:r>/judge")
    def judge(c, r):
        game = current_game()
        if game.question(c, r) is None:
            abort(404)
        verdict = form(JudgeForm)
        if game.judge(c, r, verdict.player, verdict.result):
            return redirect(url_for("board"))
        # Keep the answer visible if it was already revealed when the host judged.
        reveal = {"reveal": "1"} if verdict.reveal else {}
        return redirect(url_for("question", c=c, r=r, **reveal))

    @app.get("/regler")
    def rules():
        game = current_game()
        values = [q["value"] for cat in game.quiz["categories"] for q in cat["questions"]]
        return render_template("rules.html", values=values)

    # Host view for a second device: read-only, follows the TV live.
    def host_context():
        game = kviss.game
        ctx = {"current": None, "version": game.version if game else 0}
        if game and game.current:
            c, r, test = game.current
            ctx["current"] = {
                "c": c, "r": r, "q": game.question(c, r),
                # The TV shows the Daily Double as soon as the tile is opened, so the host may see it too.
                "daily_double": game.is_daily_double(c, r) and not test, "wager": game.wager(c, r),
                "category": game.quiz["categories"][c]["name"],
                "wrong": [game.quiz["players"][p] for p in game.wrong_players(c, r)],
                "used": game.is_used(c, r), "winner": game.winner(c, r),
            }
        return ctx

    @app.get("/vert")
    def host():
        return render_template("host.html", **host_context())

    @app.get("/vert/panel")
    def host_panel():
        # Polled by app.js. 204 = nothing changed since the version the page already shows.
        ctx = host_context()
        if request.args.get("v") == str(ctx["version"]):
            return Response(status=204)
        resp = Response(render_template("_host_panel.html", **ctx))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/media/<path:name>")
    def media(name):
        # Only files the current quiz refers to; send_from_directory also refuses paths
        # outside the folder. It answers Range requests, which Safari needs to play and seek audio.
        game = kviss.game
        if game is None or name not in game.audio_files:
            abort(404)
        return send_from_directory(kviss.media_dir, name)

    # --- starting a game ---------------------------------------------------

    @app.get("/nytt")
    def new_game():
        return render_template("new_game.html", quizzes=store.quizzes())

    @app.route("/nytt/<slug>", methods=["GET", "POST"])
    def new_game_players(slug):
        chosen = store.quiz(slug)
        if chosen is None:
            abort(404)
        game = kviss.game
        if request.method == "GET":
            players = chosen["players"] or store.last_players()
            name = f"{chosen['title']} · {datetime.now(tz):%d.%m.%Y}"  # a suggestion the host can change
            return render_template("new_game_players.html", chosen=chosen, name=name, players="\n".join(players))
        try:
            entry = parse_form(NewGameForm, request.form.to_dict())
            if entry.daily_doubles > chosen["questions"]:
                raise FormError(f"Kvissen har bare {chosen['questions']} spørsmål, så det kan ikke være flere "
                                "Dagens dobbel enn det.")
            if game and game.in_progress() and not entry.confirm:
                raise FormError("Kryss av for å avslutte spillet som pågår.")
        except FormError as e:
            return render_template("new_game_players.html", chosen=chosen, name=request.form.get("name", "").strip(),
                                   players=request.form.get("players", "").strip(), error=str(e),
                                   daily_double=request.form.get("daily_double") == "yes",
                                   daily_doubles=request.form.get("daily_doubles", "1")), 400
        if kviss.start(slug, entry.players, entry.name, entry.daily_doubles) is None:  # deleted in the meantime
            abort(404)
        return redirect(url_for("board"))

    # --- admin -------------------------------------------------------------

    @app.get("/admin")
    def admin():
        past = [Game(row) for row in store.games()]
        return render_template("admin.html", past=past)

    @app.post("/adjust")
    def adjust():
        change = form(AdjustForm)
        current_game().adjust(change.player, change.delta)
        return redirect(url_for("admin"))

    @app.post("/undo")
    def undo():
        game = current_game()
        target = form(UndoForm).next
        game.undo()
        return redirect(url_for(target))

    @app.post("/reset")
    def reset():
        game = current_game()
        form(ConfirmForm)
        game.reset()
        return redirect(url_for("board"))

    # --- JSON API for the quiz library ---------------------------------------
    # Same password as the rest of the app; anything beyond that is up to the proxy in front.

    def api_error(message, status):
        return jsonify(error=message), status

    def summary(q):
        return {"slug": q["slug"], "title": q["title"], "categories": len(q["categories"]),
                "questions": q["questions"], "music": q["music"], "players": q["players"],
                "updated_at": q["updated_at"], "plays": q["plays"], "last_played": q["last_played"]}

    @app.get("/api/quizzes")
    def api_quizzes():
        return jsonify([summary(q) for q in store.quizzes()])

    @app.post("/api/quizzes")
    def api_save_quiz():
        try:
            data = json.loads(request.get_data())
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return api_error(f"invalid JSON: {e}", 400)
        except RecursionError:
            return api_error("invalid JSON: nested too deeply", 400)
        try:
            quiz = validate_quiz(data, kviss.media_dir)
        except ConfigError as e:
            return jsonify(error=str(e), problems=e.problems), 400
        created = store.save_quiz(quiz)
        body = {**summary(store.quiz(quiz["slug"])), "created": created}
        return jsonify(body), 201 if created else 200

    @app.get("/api/quizzes/<slug>")
    def api_quiz(slug):
        # The quiz exactly as it can be uploaded again: download, edit, re-upload.
        q = store.quiz(slug)
        if q is None:
            return api_error(f"no quiz {slug!r}", 404)
        return jsonify({k: q[k] for k in ("slug", "title", "players", "categories")})

    @app.delete("/api/quizzes/<slug>")
    def api_delete_quiz(slug):
        if not store.delete_quiz(slug):
            return api_error(f"no quiz {slug!r}", 404)
        return Response(status=204)

    return app


def main(argv):
    """`python app.py` runs a development server; `python app.py import a.json b.json` adds quizzes."""
    if argv[:1] == ["import"] and argv[1:]:
        store, media_dir = Store(default_db()), default_media()
        failed = False
        for path in argv[1:]:
            try:
                quiz = load_quiz(path, media_dir)
            except (ConfigError, OSError) as e:
                print(f"{path}: {e}", file=sys.stderr)
                failed = True
                continue
            created = store.save_quiz(quiz)
            print(f"{path}: {'added' if created else 'replaced'} '{quiz['slug']}' ({quiz['title']})")
        return 1 if failed else 0
    if argv:
        print("usage: python app.py [import QUIZ.json ...]", file=sys.stderr)
        return 2
    # Local development only; use gunicorn in production (see README).
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), debug=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
