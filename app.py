"""Kviss - a Jeopardy-style quiz for one host screen (phone mirrored to a TV).

The quiz (title, players, categories, questions) is read from a static JSON
file. Game progress is kept server-side and persisted to a state file, so a
reloaded phone browser or a restarted server picks up where the game left off.
"""

import copy
import hashlib
import json
import mimetypes
import os
import re
import secrets
import threading
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, abort, redirect, render_template, request, send_from_directory, url_for

BASE_DIR = Path(__file__).resolve().parent
mimetypes.add_type("application/manifest+json", ".webmanifest")
UNDO_LIMIT = 50
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
AUDIO_TYPES = {".mp3", ".m4a", ".aac", ".wav"}  # formats both Safari and Chrome play


class ConfigError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise ConfigError(msg)


def _is_seconds(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0


def _check_music(q, where, media_dir):
    """Validate the optional music fields of a question. Returns the audio file name, if any."""
    youtube, audio = q.get("youtube"), q.get("audio")
    if youtube is None and audio is None:
        for field in ("start", "end"):
            _require(field not in q, f"{where}: '{field}' needs 'youtube' or 'audio'")
        return None
    _require(youtube is None or audio is None, f"{where}: use either 'youtube' or 'audio', not both")
    if youtube is not None:
        _require(isinstance(youtube, str) and YOUTUBE_ID.fullmatch(youtube),
                 f"{where}: 'youtube' must be an 11-character video ID like 'dQw4w9WgXcQ' "
                 f"(the part after v= in the link), got {youtube!r}")
    else:
        _require(isinstance(audio, str) and audio.strip(), f"{where}: 'audio' must be a file name")
        _require(Path(audio).suffix.lower() in AUDIO_TYPES,
                 f"{where}: 'audio' must be one of {', '.join(sorted(AUDIO_TYPES))}, got {audio!r}")
        file = (media_dir / audio).resolve()
        _require(".." not in Path(audio).parts and file.is_relative_to(media_dir.resolve()),
                 f"{where}: 'audio' must be inside {media_dir}")
        q["audio"] = audio = Path(audio).as_posix()  # "./a.mp3" -> "a.mp3", the path browsers request
        _require(file.is_file(), f"{where}: audio file not found: {media_dir / audio}")
    start, end = q.get("start", 0), q.get("end")
    _require(_is_seconds(start), f"{where}: 'start' must be a number of seconds (0 or more)")
    _require(end is None or (_is_seconds(end) and end > start),
             f"{where}: 'end' must be a number of seconds after 'start'")
    return audio


def load_quiz(path):
    """Read and validate the quiz file. Raises ConfigError with a readable message.

    Local audio files for music questions live in a 'media' folder next to the quiz file.
    """
    path = Path(path)
    media_dir = path.parent / "media"
    audio_files = set()
    raw = path.read_bytes()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: invalid JSON: {e}") from e

    _require(isinstance(data, dict), "top level must be an object")
    title = data.get("title", "Kviss")
    _require(isinstance(title, str), "'title' must be a string")

    players = data.get("players")
    _require(isinstance(players, list) and players, "'players' must be a non-empty list")
    for p in players:
        _require(isinstance(p, str) and p.strip(), f"player names must be non-empty strings, got {p!r}")
    _require(len(set(players)) == len(players), "player names must be unique")

    categories = data.get("categories")
    _require(isinstance(categories, list) and categories, "'categories' must be a non-empty list")
    for ci, cat in enumerate(categories, 1):
        where = f"category #{ci}"
        _require(isinstance(cat, dict), f"{where} must be an object")
        _require(isinstance(cat.get("name"), str) and cat["name"].strip(), f"{where}: 'name' is required")
        where = f"category {cat['name']!r}"
        questions = cat.get("questions")
        _require(isinstance(questions, list) and questions, f"{where}: 'questions' must be a non-empty list")
        for qi, q in enumerate(questions, 1):
            qwhere = f"{where}, question #{qi}"
            _require(isinstance(q, dict), f"{qwhere} must be an object")
            value = q.get("value")
            _require(isinstance(value, int) and not isinstance(value, bool) and value > 0,
                     f"{qwhere}: 'value' must be a positive integer")
            for field in ("question", "answer"):
                _require(isinstance(q.get(field), str) and q[field].strip(), f"{qwhere}: '{field}' is required")
            audio = _check_music(q, qwhere, media_dir)
            if audio:
                audio_files.add(audio)

    return {
        "title": title,
        "players": players,
        "categories": categories,
        "media_dir": media_dir,
        "audio_files": audio_files,  # the only files /media/ will serve
        # Changing the quiz file invalidates any saved game state.
        "fingerprint": hashlib.sha256(raw).hexdigest(),
    }


class Game:
    """All mutable game state, guarded by a lock and saved after every change."""

    def __init__(self, quiz, state_path):
        self.quiz = quiz
        self.state_path = Path(state_path)
        self.lock = threading.Lock()
        self.state = self._load()
        # Live, in-memory only (not saved, not undoable): the question the TV is
        # showing, and a counter that changes whenever anything a viewer sees changes.
        self.current = None
        self.version = secrets.randbelow(1 << 30)  # random start: no clash after a restart

    # --- persistence -------------------------------------------------------

    def _fresh(self):
        return {
            "fingerprint": self.quiz["fingerprint"],
            "scores": [0] * len(self.quiz["players"]),
            "used": {},     # "c-r" -> index of player who answered correctly, or None
            "wrong": {},    # "c-r" -> [indexes of players who answered wrong]
            "history": [],  # snapshots for undo
        }

    def _load(self):
        try:
            state = json.loads(self.state_path.read_text())
            if state.get("fingerprint") == self.quiz["fingerprint"]:
                return state
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return self._fresh()

    def _save(self):
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state))
        os.replace(tmp, self.state_path)  # atomic: never leaves a half-written file
        self.version += 1

    def _checkpoint(self):
        snap = {k: copy.deepcopy(self.state[k]) for k in ("scores", "used", "wrong")}
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

    def can_undo(self):
        return bool(self.state["history"])

    def standings(self):
        players = [{"name": n, "score": s} for n, s in zip(self.quiz["players"], self.state["scores"])]
        players.sort(key=lambda p: p["score"], reverse=True)
        # Tied scores share a place, and the next place is skipped (1, 1, 3).
        for i, p in enumerate(players):
            tied = i > 0 and p["score"] == players[i - 1]["score"]
            p["place"] = players[i - 1]["place"] if tied else i + 1
        return players

    # --- actions -----------------------------------------------------------

    def set_current(self, c=None, r=None):
        """Remember which question the TV shows (None = the board), for the host view."""
        current = None if c is None else (c, r)
        with self.lock:
            if current != self.current:
                self.current = current
                self.version += 1

    def judge(self, c, r, player, result):
        """Apply a host decision. Returns True if the question is now finished."""
        q = self.question(c, r)
        key = f"{c}-{r}"
        with self.lock:
            if key in self.state["used"]:
                return True
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
        with self.lock:
            self.state = self._fresh()
            self._save()


def create_app(config_path=None, state_path=None, password=None):
    config_path = config_path or os.environ.get("KVISS_CONFIG", BASE_DIR / "quiz.json")
    state_path = state_path or os.environ.get("KVISS_STATE", BASE_DIR / "state.json")
    password = password if password is not None else os.environ.get("KVISS_PASSWORD", "")

    app = Flask(__name__)
    game = Game(load_quiz(config_path), state_path)
    app.config["GAME"] = game

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

    @app.context_processor
    def inject():
        return {"game": game, "quiz": game.quiz}

    @app.get("/")
    def board():
        game.set_current(None)
        if game.is_over():
            return render_template("final.html", ranking=game.standings())
        cats = game.quiz["categories"]
        rows = max(len(cat["questions"]) for cat in cats)
        return render_template("board.html", rows=rows)

    @app.get("/q/<int:c>/<int:r>")
    def question(c, r):
        q = game.question(c, r)
        if q is None:
            abort(404)
        game.set_current(c, r)
        reveal = request.args.get("reveal") == "1" or game.is_used(c, r)
        return render_template("question.html", c=c, r=r, q=q,
                               category=game.quiz["categories"][c]["name"], reveal=reveal)

    @app.post("/q/<int:c>/<int:r>/judge")
    def judge(c, r):
        if game.question(c, r) is None:
            abort(404)
        player = request.form.get("player", "-1")
        finished = game.judge(c, r, int(player) if player.lstrip("-").isdigit() else -1,
                              request.form.get("result", ""))
        if finished:
            return redirect(url_for("board"))
        # Keep the answer visible if it was already revealed when the host judged.
        reveal = {"reveal": "1"} if request.form.get("reveal") == "1" else {}
        return redirect(url_for("question", c=c, r=r, **reveal))

    @app.get("/regler")
    def rules():
        values = [q["value"] for cat in game.quiz["categories"] for q in cat["questions"]]
        return render_template("rules.html", values=values)

    # Host view for a second device: read-only, follows the TV live.
    def host_context():
        ctx = {"current": None, "version": game.version}
        if game.current:
            c, r = game.current
            ctx["current"] = {
                "c": c, "r": r, "q": game.question(c, r),
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
        if request.args.get("v") == str(game.version):
            return Response(status=204)
        resp = Response(render_template("_host_panel.html", **host_context()))
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/media/<path:name>")
    def media(name):
        # Only files the quiz refers to; send_from_directory also refuses paths outside
        # the folder. It answers Range requests, which Safari needs to play and seek audio.
        if name not in game.quiz["audio_files"]:
            abort(404)
        return send_from_directory(game.quiz["media_dir"], name)

    @app.get("/admin")
    def admin():
        return render_template("admin.html")

    @app.post("/adjust")
    def adjust():
        try:
            game.adjust(int(request.form["player"]), int(request.form["delta"]))
        except (KeyError, ValueError):
            abort(400)
        return redirect(url_for("admin"))

    @app.post("/undo")
    def undo():
        game.undo()
        return redirect(request.form.get("next") == "admin" and url_for("admin") or url_for("board"))

    @app.post("/reset")
    def reset():
        if request.form.get("confirm") != "yes":
            abort(400)
        game.reset()
        return redirect(url_for("board"))

    return app


if __name__ == "__main__":
    # Local development only; use gunicorn in production (see README).
    create_app().run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")), debug=True)
