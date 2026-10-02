"""Kviss - a Jeopardy-style quiz for one host screen (phone mirrored to a TV).

The quiz (title, players, categories, questions) is read from a static JSON
file. Game progress is kept server-side and persisted to a state file, so a
reloaded phone browser or a restarted server picks up where the game left off.
"""

import copy
import hashlib
import json
import os
import secrets
import threading
from pathlib import Path
from urllib.parse import urlparse

from flask import Flask, Response, abort, redirect, render_template, request, url_for

BASE_DIR = Path(__file__).resolve().parent
UNDO_LIMIT = 50


class ConfigError(ValueError):
    pass


def _require(cond, msg):
    if not cond:
        raise ConfigError(msg)


def load_quiz(path):
    """Read and validate the quiz file. Raises ConfigError with a readable message."""
    path = Path(path)
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

    return {
        "title": title,
        "players": players,
        "categories": categories,
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
        return sorted(players, key=lambda p: p["score"], reverse=True)

    # --- actions -----------------------------------------------------------

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
                return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="kviss"'})
        # Reject cross-site form posts (CSRF): browsers send Origin on POST.
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlparse(origin).netloc != request.host:
                abort(403)

    @app.context_processor
    def inject():
        return {"game": game, "quiz": game.quiz}

    @app.get("/")
    def board():
        cats = game.quiz["categories"]
        rows = max(len(cat["questions"]) for cat in cats)
        return render_template("board.html", rows=rows)

    @app.get("/q/<int:c>/<int:r>")
    def question(c, r):
        q = game.question(c, r)
        if q is None:
            abort(404)
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
