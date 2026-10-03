import base64
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from app import BASE_DIR, ConfigError, create_app, load_quiz, main
from schemas import slugify

QUIZ = {
    "title": "Test",
    "players": ["A", "B"],
    "categories": [
        {"name": "Cat1", "questions": [
            {"value": 100, "question": "Q1", "answer": "A1"},
            {"value": 200, "question": "Q2", "answer": "A2"},
        ]},
        {"name": "Cat2", "questions": [
            {"value": 100, "question": "<script>x</script>", "answer": "A3"},
        ]},
    ],
}


class KvissTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "kviss.db"
        self.media = self.tmp / "media"
        self.client = self.make_client()
        self.upload(QUIZ)
        self.start("test", ["A", "B"])

    def make_client(self, password="", seed=()):
        self.app = create_app(self.db, password=password, media_dir=self.media, seed=list(seed))
        return self.app.test_client()

    @property
    def game(self):
        return self.app.config["KVISS"].game

    def upload(self, quiz, client=None):
        return (client or self.client).post("/api/quizzes", json=quiz)

    def start(self, slug, players, **form):
        return self.client.post(f"/nytt/{slug}", data={"players": "\n".join(players), **form})

    def judge(self, c, r, result, player=None):
        data = {"result": result}
        if player is not None:
            data["player"] = str(player)
        return self.client.post(f"/q/{c}/{r}/judge", data=data)

    def test_example_quizzes_are_valid(self):
        load_quiz(BASE_DIR / "quiz.json")
        load_quiz(BASE_DIR / "quiz-example.json")

    def test_board_renders_and_escapes(self):
        self.assertEqual(self.client.get("/brett").status_code, 200)
        page = self.client.get("/q/1/0").get_data(as_text=True)
        self.assertNotIn("<script>x", page)
        self.assertIn("&lt;script&gt;", page)

    def test_answer_hidden_until_revealed(self):
        self.assertIn('class="clue-answer" hidden>A1', self.client.get("/q/0/0").get_data(as_text=True))
        self.assertIn('class="clue-answer">A1', self.client.get("/q/0/0?reveal=1").get_data(as_text=True))

    def test_standings_share_place_on_tie(self):
        self.judge(0, 0, "correct", 0)
        self.judge(1, 0, "correct", 1)
        self.assertEqual([p["place"] for p in self.game.standings()], [1, 1])

    def test_final_screen_podium(self):
        self.judge(0, 1, "correct", 1)
        for c, r in [(0, 0), (1, 0)]:
            self.judge(c, r, "nobody")
        page = self.client.get("/brett").get_data(as_text=True)
        self.assertIn("step slot-1 rank-1", page)
        self.assertNotIn('class="scores"', page)

    def test_classic_scoring(self):
        self.judge(0, 1, "wrong", 0)
        self.assertEqual(self.game.state["scores"], [-200, 0])
        self.judge(0, 1, "wrong", 0)  # same player can't answer twice
        self.assertEqual(self.game.state["scores"], [-200, 0])
        resp = self.judge(0, 1, "correct", 1)
        self.assertEqual(resp.headers["Location"], "/brett")
        self.assertEqual(self.game.state["scores"], [-200, 200])
        self.judge(0, 1, "correct", 0)  # question already used
        self.assertEqual(self.game.state["scores"], [-200, 200])

    def test_undo_and_game_over(self):
        self.judge(0, 0, "correct", 0)
        self.client.post("/undo")
        self.assertEqual(self.game.state["scores"], [0, 0])
        self.assertFalse(self.game.is_used(0, 0))
        for c, r in [(0, 0), (0, 1), (1, 0)]:
            self.judge(c, r, "nobody")
        self.assertIn("Sluttresultat", self.client.get("/brett").get_data(as_text=True))

    def test_state_survives_restart(self):
        self.judge(0, 0, "correct", 1)
        self.make_client()
        self.assertEqual(self.game.state["scores"], [0, 100])
        self.assertEqual(self.game.quiz["players"], ["A", "B"])

    def test_reset_requires_confirm(self):
        self.judge(0, 0, "correct", 0)
        self.assertEqual(self.client.post("/reset").status_code, 400)
        self.client.post("/reset", data={"confirm": "yes"})
        self.assertEqual(self.game.state["scores"], [0, 0])

    def test_password_and_cross_site_post(self):
        client = self.make_client(password="s3cret")
        self.assertEqual(client.get("/").status_code, 401)
        auth = {"Authorization": "Basic " + base64.b64encode(b"host:s3cret").decode()}
        self.assertEqual(client.get("/", headers=auth).status_code, 200)
        resp = client.post("/undo", headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 403)

    def test_rules_page(self):
        page = self.client.get("/regler").get_data(as_text=True)
        self.assertIn("Slik spiller vi", page)
        self.assertIn("2 kategorier · 3 spørsmål", page)
        self.assertIn('href="/regler"', self.client.get("/brett").get_data(as_text=True))

    def test_host_view_follows_tv(self):
        self.assertIn("Brettet vises", self.client.get("/vert").get_data(as_text=True))
        self.client.get("/q/0/1")  # TV opens a question
        page = self.client.get("/vert").get_data(as_text=True)
        self.assertIn("Q2", page)
        self.assertIn("A2", page)
        v = str(self.game.version)
        self.assertEqual(self.client.get(f"/vert/panel?v={v}").status_code, 204)
        self.judge(0, 1, "wrong", 0)  # any change bumps the version
        panel = self.client.get(f"/vert/panel?v={v}")
        self.assertEqual(panel.status_code, 200)
        self.assertIn("Feil: A", panel.get_data(as_text=True))
        self.client.get("/brett")  # TV back on the board
        self.assertIn("Brettet vises", self.client.get("/vert/panel").get_data(as_text=True))

    def test_bad_config_message(self):
        bad = self.tmp / "bad.json"
        bad.write_text(json.dumps({**QUIZ, "players": ["A", "A"]}))
        with self.assertRaisesRegex(ConfigError, "unique"):
            load_quiz(bad)
        bad.write_text("{nope")
        with self.assertRaisesRegex(ConfigError, "invalid JSON"):
            load_quiz(bad)

    # --- music questions ---------------------------------------------------

    def music_quiz(self, *extra):
        """Upload and start a quiz whose first category has the given music questions."""
        self.media.mkdir(exist_ok=True)
        (self.media / "song.mp3").write_bytes(b"ID3" + bytes(range(256)) * 4)
        quiz = json.loads(json.dumps(QUIZ))
        for q in extra:
            quiz["categories"][0]["questions"].append({"value": 300, "question": "Låt?", "answer": "Svar", **q})
        resp = self.upload(quiz)
        if resp.status_code >= 400:
            raise ConfigError(resp.get_json()["error"])
        self.start("test", ["A", "B"])
        return self.client

    def assert_bad_music(self, fields, message):
        with self.assertRaisesRegex(ConfigError, message):
            self.music_quiz(fields)

    def test_music_validation(self):
        self.music_quiz({"youtube": "dQw4w9WgXcQ", "start": 30, "end": 45.5}, {"audio": "song.mp3"})
        self.assert_bad_music({"youtube": "https://youtu.be/dQw4w9WgXcQ"}, "11-character video ID")
        self.assert_bad_music({"youtube": "dQw4w9WgXc\"><x"}, "11-character video ID")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "audio": "song.mp3"}, "not both")
        self.assert_bad_music({"audio": "missing.mp3"}, "not found")
        self.assert_bad_music({"audio": "../kviss.db"}, "must be one of")
        self.assert_bad_music({"audio": "../media/song.mp3"}, "inside")
        self.assert_bad_music({"audio": "/etc/song.mp3"}, "inside")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": 20, "end": 10}, "'end'")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": -1}, "'start'")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": True}, "'start'")
        self.assert_bad_music({"start": 10}, "needs 'youtube' or 'audio'")
        self.assert_bad_music({"audio": "song\x00.mp3"}, "file name")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": "10"}, "'start' must be a number")

    def test_youtube_question_page(self):
        client = self.music_quiz({"youtube": "dQw4w9WgXcQ", "start": 30, "end": 45})
        page = client.get("/q/0/2").get_data(as_text=True)
        self.assertIn('data-youtube="dQw4w9WgXcQ"', page)
        self.assertIn('data-start="30" data-end="45"', page)
        self.assertNotIn("<iframe", page)  # the player is created hidden by app.js
        self.assertIn('class="clue-answer" hidden>Svar', page)
        self.assertNotIn("class=\"music", client.get("/q/0/0").get_data(as_text=True))

    def test_audio_question_and_media_route(self):
        client = self.music_quiz({"audio": "song.mp3", "start": 5})
        page = client.get("/q/0/2").get_data(as_text=True)
        self.assertIn('data-audio="/media/song.mp3"', page)
        self.assertIn('src="/media/song.mp3#t=5"', page)  # no-JS fallback starts at `start`
        resp = client.get("/media/song.mp3", headers={"Range": "bytes=0-9"})
        self.assertEqual(resp.status_code, 206)  # Safari needs Range support for audio
        self.assertEqual(resp.data, b"ID3" + bytes(range(7)))
        resp.close()
        # Only files the quiz uses are served.
        (self.media / "other.mp3").write_bytes(b"x")
        self.assertEqual(client.get("/media/other.mp3").status_code, 404)
        self.assertEqual(client.get("/media/../kviss.db").status_code, 404)

    def test_audio_name_is_normalized(self):
        client = self.music_quiz({"audio": "./song.mp3"})
        self.assertIn('data-audio="/media/song.mp3"', client.get("/q/0/2").get_data(as_text=True))
        resp = client.get("/media/song.mp3")
        self.assertEqual(resp.status_code, 200)
        resp.close()

    def test_media_needs_password(self):
        self.music_quiz({"audio": "song.mp3"})
        client = self.make_client(password="s3cret")
        self.assertEqual(client.get("/media/song.mp3").status_code, 401)

    def test_music_on_host_view_and_admin(self):
        client = self.music_quiz({"youtube": "dQw4w9WgXcQ", "start": 75, "end": 90})
        client.get("/q/0/2")
        self.assertIn("♪ YouTube dQw4w9WgXcQ,\n    1:15–1:30", client.get("/vert").get_data(as_text=True))
        admin = client.get("/admin").get_data(as_text=True)
        self.assertIn('href="/q/0/2"', admin)
        self.assertIn("Svar", admin)

    # --- quiz library and games ----------------------------------------------

    def test_no_game_goes_to_landing_page(self):
        self.db.unlink()
        client = self.make_client()
        self.assertIsNone(self.game)
        self.assertEqual(client.get("/brett").headers["Location"], "/")
        self.assertEqual(client.get("/q/0/0").headers["Location"], "/")
        self.assertEqual(client.post("/undo").headers["Location"], "/")
        home = client.get("/").get_data(as_text=True)
        self.assertIn("Ingen spill pågår", home)
        self.assertIn('href="/nytt"', home)
        self.assertIn("Ingen ferdige spill", home)
        self.assertIn("Ingen kviss er lagt inn", client.get("/nytt").get_data(as_text=True))
        self.assertIn("Ingen spill pågår", client.get("/vert").get_data(as_text=True))
        self.assertIn("Ingen spill pågår", client.get("/admin").get_data(as_text=True))
        self.assertEqual(client.get("/media/song.mp3").status_code, 404)

    def test_new_game_screen_lists_quizzes(self):
        self.upload({**QUIZ, "title": "Fredagskviss på Bærum", "players": ["Rød", "Blå"]})
        page = self.client.get("/nytt").get_data(as_text=True)
        self.assertIn('href="/nytt/fredagskviss-pa-baerum"', page)
        self.assertIn("2 kategorier · 3 spørsmål", page)
        self.assertIn("spilt 1 gang", page)  # the quiz started in setUp
        self.assertIn("aldri spilt", page)
        # Suggested players from the quiz are pre-filled; otherwise the last game's players.
        self.assertIn(">Rød\nBlå</textarea>", self.client.get("/nytt/fredagskviss-pa-baerum").get_data(as_text=True))
        self.upload({**QUIZ, "title": "Uten lag"})
        self.assertIn(">A\nB</textarea>", self.client.get("/nytt/uten-lag").get_data(as_text=True))
        self.assertEqual(self.client.get("/nytt/nope").status_code, 404)

    def test_rerun_quiz_with_other_players_keeps_history(self):
        self.judge(0, 0, "correct", 0)
        for c, r in [(0, 1), (1, 0)]:
            self.judge(c, r, "nobody")
        self.assertIn("Nytt spill", self.client.get("/brett").get_data(as_text=True))  # podium links to it
        first = self.game.id
        resp = self.start("test", ["Ola", "Kari", "Per"])
        self.assertEqual(resp.headers["Location"], "/brett")
        self.assertNotEqual(self.game.id, first)
        self.assertEqual(self.game.quiz["players"], ["Ola", "Kari", "Per"])
        self.assertEqual(self.game.state["scores"], [0, 0, 0])
        self.assertIn("Ola", self.client.get("/brett").get_data(as_text=True))
        admin = self.client.get("/admin").get_data(as_text=True)
        self.assertIn("1. A <strong>100</strong> · 2. B <strong>0</strong>", admin)
        self.assertIn("(pågår)", admin)
        with sqlite3.connect(self.db) as db:
            ended = db.execute("SELECT ended_at IS NOT NULL FROM games ORDER BY id").fetchall()
        self.assertEqual(ended, [(1,), (0,)])

    def test_landing_page_shows_current_and_past_games(self):
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn("Pågående spill", home)
        self.assertIn("0 av 3 spørsmål spilt", home)
        self.assertIn('href="/brett">Fortsett →', home)
        self.assertIn('href="/nytt"', home)
        self.assertIn("Ingen ferdige spill", home)  # the current game isn't listed as a past one
        self.judge(0, 0, "correct", 1)
        first = self.game.id
        self.start("test", ["C", "D"], confirm="yes")
        home = self.client.get("/").get_data(as_text=True)
        self.assertIn(f'href="/resultat/{first}"', home)
        self.assertIn("🏆 B", home)
        self.assertIn("avsluttet etter 1 av 3 spørsmål", home)
        for c, r in [(0, 0), (0, 1), (1, 0)]:
            self.judge(c, r, "nobody")
        self.assertIn('href="/brett">Se sluttresultat →', self.client.get("/").get_data(as_text=True))

    def test_landing_page_names_every_tied_winner(self):
        self.judge(0, 0, "correct", 0)
        self.judge(1, 0, "correct", 1)
        self.start("test", ["C", "D"], confirm="yes")
        self.assertIn("🏆 A og B", self.client.get("/").get_data(as_text=True))

    def test_results_page(self):
        self.judge(0, 0, "wrong", 0)
        self.judge(0, 0, "correct", 1)
        self.judge(0, 1, "nobody")
        first = self.game.id
        page = self.client.get(f"/resultat/{first}").get_data(as_text=True)
        self.assertIn("pågår", page)  # the current game, read live
        self.assertIn('href="/brett">Fortsett', page)
        self.start("test", ["C", "D"], confirm="yes")
        page = self.client.get(f"/resultat/{first}").get_data(as_text=True)
        self.assertIn("avsluttet", page)
        self.assertNotIn("Fortsett", page)
        self.assertIn("1.</span> B", page)
        self.assertIn('class="negative">-100', page)
        self.assertIn("✓ B", page)
        self.assertIn("✗ A", page)
        self.assertIn("Ingen", page)  # (0, 1): nobody
        self.assertIn('class="unplayed"', page)  # (1, 0) was never played
        self.assertIn("&lt;script&gt;", page)  # question text is escaped in the tooltip
        self.assertIn(f'href="/resultat/{first}"', self.client.get("/admin").get_data(as_text=True))
        self.assertEqual(self.client.get("/resultat/999").status_code, 404)

    def test_game_name(self):
        page = self.client.get("/nytt/test").get_data(as_text=True)
        self.assertRegex(page, r'name="name" type="text" maxlength="80" value="Test · \d\d\.\d\d\.\d{4}"')
        self.judge(0, 0, "correct", 0)
        first = self.game.id
        self.start("test", ["C", "D"], name="  Julebord <2026>  ", confirm="yes")
        self.assertEqual(self.game.name, "Julebord <2026>")  # trimmed, stored as typed
        for url in ("/brett", "/", "/vert", "/admin", f"/resultat/{self.game.id}"):
            page = self.client.get(url).get_data(as_text=True)
            self.assertIn("Julebord &lt;2026&gt;", page, url)
            self.assertNotIn("Julebord <2026>", page, url)
        self.assertIn("<title>Julebord &lt;2026&gt;</title>", self.client.get("/brett").get_data(as_text=True))
        self.assertIn('<span class="muted">Test</span>', self.client.get("/").get_data(as_text=True))  # the quiz
        # Left empty, the game is named after the quiz.
        self.assertEqual(self.client.get(f"/resultat/{first}").status_code, 200)
        self.start("test", ["E", "F"], name="   ")
        self.assertEqual(self.game.name, "Test")
        with sqlite3.connect(self.db) as db:
            names = db.execute("SELECT name FROM games ORDER BY id").fetchall()
        # setUp's game (no name given) and the new one; the untouched Julebord game was dropped.
        self.assertEqual(names, [(None,), (None,)])

    def test_game_name_too_long(self):
        resp = self.start("test", ["C", "D"], name="x" * 81)
        self.assertEqual(resp.status_code, 400)
        page = resp.get_data(as_text=True)
        self.assertIn("maks 80 tegn", page)
        self.assertIn('value="' + "x" * 81 + '"', page)  # what was typed is kept
        self.assertEqual(self.game.quiz["players"], ["A", "B"])

    def test_database_from_before_game_names(self):
        self.db.unlink()
        with sqlite3.connect(self.db) as db:  # the version 1 schema, with a game in progress
            db.executescript("""
                CREATE TABLE quizzes (id INTEGER PRIMARY KEY, slug TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE games (id INTEGER PRIMARY KEY, quiz_id INTEGER REFERENCES quizzes(id) ON DELETE SET NULL,
                    quiz TEXT NOT NULL, players TEXT NOT NULL, state TEXT NOT NULL, started_at TEXT NOT NULL,
                    ended_at TEXT);
                PRAGMA user_version = 1;
            """)
            db.execute("INSERT INTO games (quiz, players, state, started_at) VALUES (?, ?, ?, ?)",
                       (json.dumps({k: v for k, v in QUIZ.items() if k != "players"}), '["A", "B"]',
                        json.dumps({"scores": [100, 0], "used": {"0-0": 0}, "wrong": {}, "history": [{}]}),
                        "2026-01-01T20:00:00+00:00"))
        client = self.make_client()
        self.assertEqual(self.game.name, "Test")
        self.assertIn("<h1>Test</h1>", client.get("/brett").get_data(as_text=True))
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
        self.make_client()  # opening it again doesn't try to add the column twice

    def test_untouched_game_is_not_kept(self):
        self.start("test", ["C", "D"])
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM games").fetchone()[0], 1)

    def test_only_one_current_game(self):
        with sqlite3.connect(self.db) as db, self.assertRaises(sqlite3.IntegrityError):
            db.execute("INSERT INTO games (quiz, players, state, started_at) VALUES ('{}', '[]', '{}', 'x')")

    def test_ending_a_game_in_progress_needs_confirm(self):
        self.judge(0, 0, "correct", 0)
        resp = self.start("test", ["C", "D"])
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Kryss av", resp.get_data(as_text=True))
        self.assertEqual(self.game.quiz["players"], ["A", "B"])
        self.start("test", ["C", "D"], confirm="yes")
        self.assertEqual(self.game.quiz["players"], ["C", "D"])

    def test_player_names_are_checked(self):
        for names, message in [([" ", ""], "minst én"), (["Ola", "ola"], "samme navn"),
                               (["x" * 41], "maks 40"), ([str(i) for i in range(31)], "Maks 30")]:
            resp = self.start("test", names)
            self.assertEqual(resp.status_code, 400)
            self.assertIn(message, resp.get_data(as_text=True))
        self.start("test", ["  Ola  ", "", "Kari"])
        self.assertEqual(self.game.quiz["players"], ["Ola", "Kari"])

    def test_editing_a_quiz_does_not_change_running_game(self):
        self.judge(0, 0, "correct", 0)
        changed = json.loads(json.dumps(QUIZ))
        changed["categories"][0]["questions"][1]["question"] = "Changed"
        resp = self.upload(changed)
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.get_json()["created"])
        self.assertIn("Q2", self.client.get("/q/0/1").get_data(as_text=True))
        self.assertEqual(self.game.state["scores"], [100, 0])
        self.start("test", ["A", "B"], confirm="yes")
        self.assertIn("Changed", self.client.get("/q/0/1").get_data(as_text=True))

    def test_deleting_a_quiz_keeps_games(self):
        self.judge(0, 0, "correct", 1)
        self.assertEqual(self.client.delete("/api/quizzes/test").status_code, 204)
        self.assertEqual(self.client.delete("/api/quizzes/test").status_code, 404)
        self.assertIn("Q1", self.client.get("/q/0/0").get_data(as_text=True))  # still playable
        self.make_client()
        self.assertEqual(self.game.state["scores"], [0, 100])
        self.assertIn("<strong>Test</strong>", self.client.get("/admin").get_data(as_text=True))

    # --- JSON API ------------------------------------------------------------

    def test_api_upload_list_and_download(self):
        resp = self.upload({**QUIZ, "title": "Ny kviss", "slug": "ny-1"})
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json()["slug"], "ny-1")
        self.assertTrue(resp.get_json()["created"])
        listing = self.client.get("/api/quizzes").get_json()
        self.assertEqual([q["slug"] for q in listing], ["ny-1", "test"])
        self.assertEqual(listing[1]["plays"], 1)
        self.assertEqual(listing[1]["questions"], 3)
        quiz = self.client.get("/api/quizzes/ny-1").get_json()
        self.assertEqual(quiz["categories"], QUIZ["categories"])
        self.assertEqual(self.upload(quiz).status_code, 200)  # round trip: same slug, replaced
        self.assertEqual(self.client.get("/api/quizzes/nope").status_code, 404)

    def test_api_rejects_bad_quizzes(self):
        resp = self.client.post("/api/quizzes", data="{nope")
        self.assertEqual(resp.status_code, 400)
        resp = self.client.post("/api/quizzes", data="{nope", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("invalid JSON", resp.get_json()["error"])
        for quiz, message in [([], "object"), ({**QUIZ, "title": ""}, "'title'"),
                              ({**QUIZ, "title": "!!"}, "slug"), ({**QUIZ, "slug": "Bad Slug"}, "'slug'"),
                              ({**QUIZ, "categories": []}, "categories"), ({**QUIZ, "players": "A"}, "players")]:
            resp = self.upload(quiz)
            self.assertEqual(resp.status_code, 400, quiz)
            self.assertIn(message, resp.get_json()["error"])
        resp = self.client.post("/api/quizzes", data="[" * 100000, content_type="application/json")
        self.assertEqual(resp.status_code, 400)

    def test_api_quiz_types_are_strict(self):
        def question(**fields):
            return {**{"value": 100, "question": "Q", "answer": "A"}, **fields}
        quiz = {"title": "T", "categories": [{"name": "Sport", "questions": [
            question(value="100"), question(value=True), question(anwser="typo"), question(question="  ")]}]}
        resp = self.upload(quiz)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.get_json()["problems"], [  # every problem at once, located by category name
            "category 'Sport', question #1, 'value' must be a whole number",
            "category 'Sport', question #2, 'value' must be a whole number",
            "category 'Sport', question #3, 'anwser' is not a known field (check the spelling)",
            "category 'Sport', question #4, 'question' must not be empty",
        ])
        # Python's json module accepts these, but they are not numbers a clip can use.
        for number in ("Infinity", "NaN"):
            body = ('{"title": "T", "categories": [{"name": "S", "questions": [{"value": 1, "question": "q", '
                    f'"answer": "a", "youtube": "dQw4w9WgXcQ", "end": {number}}}]}}]}}')
            resp = self.client.post("/api/quizzes", data=body, content_type="application/json")
            self.assertEqual(resp.status_code, 400, number)
        resp = self.upload({**QUIZ, "title": "  Mellomrom  ", "slug": None})
        self.assertEqual(resp.get_json()["slug"], "mellomrom")
        self.assertEqual(self.client.get("/api/quizzes/mellomrom").get_json()["title"], "Mellomrom")

    def test_api_rejects_large_uploads(self):
        resp = self.client.post("/api/quizzes", data=b" " * (2 * 1024 * 1024), content_type="application/json")
        self.assertEqual(resp.status_code, 413)

    def test_tampered_forms_are_rejected(self):
        for path, data in [("/q/0/0/judge", {"result": "steal"}), ("/q/0/0/judge", {"result": "correct"}),
                           ("/q/0/0/judge", {"result": "correct", "player": "x"}),
                           ("/adjust", {"player": "0", "delta": "9" * 5000}), ("/adjust", {"player": "-1", "delta": "1"}),
                           ("/undo", {"next": "https://evil.example"})]:
            self.assertEqual(self.client.post(path, data=data).status_code, 400, (path, data))
        self.assertEqual(self.game.state["scores"], [0, 0])
        self.assertFalse(self.game.can_undo())
        self.client.post("/adjust", data={"player": "1", "delta": "-100"})
        self.assertEqual(self.game.state["scores"], [0, -100])
        self.assertEqual(self.client.post("/undo", data={"next": "admin"}).headers["Location"], "/admin")

    def upload_files(self, *files, client=None, **kwargs):
        data = {"files": [(io.BytesIO(body), name) for name, body in files]}
        return (client or self.client).post("/last-opp", data=data, content_type="multipart/form-data", **kwargs)

    def test_upload_page(self):
        page = self.client.get("/last-opp").get_data(as_text=True)
        self.assertIn('type="file"', page)
        self.assertIn("Slik lager du en kviss-fil", page)
        self.assertIn('href="/static/kviss-mal.json"', page)
        for path in ("/", "/nytt", "/admin"):
            self.assertIn('href="/last-opp"', self.client.get(path).get_data(as_text=True), path)

    def test_upload_template_is_valid(self):
        quiz = load_quiz(BASE_DIR / "static" / "kviss-mal.json")
        self.assertEqual(quiz["slug"], "min-kviss")
        resp = self.upload_files(("min-kviss.json", (BASE_DIR / "static" / "kviss-mal.json").read_bytes()))
        self.assertEqual(resp.status_code, 200)

    def test_upload_quiz_files(self):
        good = json.dumps({**QUIZ, "title": "Fredagskviss"}).encode()
        bad = json.dumps({**QUIZ, "title": "Feil", "categories": [{"name": "Sport", "questions": [
            {"value": "100", "question": "Q", "anwser": "A"}]}]}).encode()
        resp = self.upload_files(("fredag.json", good), ("feil.json", bad), ("tull.json", b"{nope"))
        self.assertEqual(resp.status_code, 200)  # one of them was saved
        page = resp.get_data(as_text=True)
        self.assertIn("✓ Fredagskviss", page)
        self.assertIn("lagt til", page)
        self.assertIn('href="/nytt/fredagskviss"', page)
        self.assertIn("✗ feil.json", page)
        self.assertIn("category &#39;Sport&#39;, question #1, &#39;value&#39; must be a whole number", page)
        self.assertIn("&#39;anwser&#39; is not a known field", page)
        self.assertIn("✗ tull.json", page)
        self.assertIn("Ikke gyldig JSON", page)
        slugs = [q["slug"] for q in self.client.get("/api/quizzes").get_json()]
        self.assertEqual(slugs, ["fredagskviss", "test"])  # the broken files changed nothing
        # The same title again replaces it, as with the API.
        resp = self.upload_files(("fredag.json", good))
        self.assertIn("erstattet", resp.get_data(as_text=True))
        # Nothing saved at all is a 400.
        self.assertEqual(self.upload_files(("tull.json", b"[")).status_code, 400)
        self.assertEqual(self.upload_files(("tull.json", b"[" * 100000)).status_code, 400)

    def test_upload_needs_a_file(self):
        resp = self.client.post("/last-opp", data={}, content_type="multipart/form-data")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Velg minst én JSON-fil", resp.get_data(as_text=True))

    def test_upload_too_large(self):
        resp = self.upload_files(("stor.json", b" " * (2 * 1024 * 1024)))
        self.assertEqual(resp.status_code, 413)
        self.assertIn("for store", resp.get_data(as_text=True))

    def test_upload_needs_password_and_same_origin(self):
        client = self.make_client(password="s3cret")
        body = json.dumps({**QUIZ, "title": "Hemmelig"}).encode()
        self.assertEqual(self.upload_files(("q.json", body), client=client).status_code, 401)
        auth = {"Authorization": "Basic " + base64.b64encode(b":s3cret").decode()}
        resp = self.upload_files(("q.json", body), client=client,
                                 headers={**auth, "Origin": "https://evil.example"})
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(self.upload_files(("q.json", body), client=client, headers=auth).status_code, 200)

    def test_api_needs_password(self):
        client = self.make_client(password="s3cret")
        self.assertEqual(client.get("/api/quizzes").status_code, 401)
        self.assertEqual(self.upload(QUIZ, client).status_code, 401)
        auth = {"Authorization": "Basic " + base64.b64encode(b":s3cret").decode()}
        self.assertEqual(client.post("/api/quizzes", json=QUIZ, headers=auth).status_code, 200)
        self.assertEqual(client.delete("/api/quizzes/test").status_code, 401)

    def test_slugify(self):
        self.assertEqual(slugify("Fredagskviss på Bærum!"), "fredagskviss-pa-baerum")
        self.assertEqual(slugify("Øl & Café 2"), "ol-cafe-2")

    # --- first start and command line ----------------------------------------

    def test_quiz_files_imported_on_first_start_only(self):
        self.db.unlink()
        broken = self.tmp / "broken.json"
        broken.write_text("{}")
        with self.assertLogs("app", "WARNING") as logs:
            self.make_client(seed=[BASE_DIR / "quiz.json", broken, self.tmp / "missing.json"])
        self.assertIn("'title' is required", logs.output[0])
        slugs = [q["slug"] for q in self.client.get("/api/quizzes").get_json()]
        self.assertEqual(len(slugs), 1)
        self.client.delete(f"/api/quizzes/{slugs[0]}")
        self.make_client(seed=[BASE_DIR / "quiz.json"])  # empty again: imports again
        self.assertEqual(len(self.client.get("/api/quizzes").get_json()), 1)
        self.upload(QUIZ)
        self.make_client(seed=[BASE_DIR / "quiz-example.json"])
        self.assertEqual(len(self.client.get("/api/quizzes").get_json()), 2)  # library not empty: no import

    def test_unknown_time_zone_falls_back_to_utc_with_warning(self):
        with mock.patch.dict(os.environ, {"KVISS_TZ": "Europe/Olso"}), self.assertLogs("app", "WARNING") as logs:
            app = create_app(db_path=self.db, password="", media_dir=self.media, seed=[])
        self.assertIn("Europe/Olso", logs.output[0])
        self.assertEqual(app.jinja_env.filters["when"]("2026-07-01T12:00:00+00:00"), "01.07.2026 12:00")

    def test_import_command(self):
        path = self.tmp / "q.json"
        path.write_text(json.dumps({**QUIZ, "title": "Fra fil"}))
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"KVISS_DB": str(self.db), "KVISS_MEDIA": str(self.media)}), \
                redirect_stdout(out):
            self.assertEqual(main(["import", str(path)]), 0)
            self.assertEqual(main(["import", str(path)]), 0)
            with mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(main(["import", str(self.tmp / "missing.json")]), 1)
        self.assertIn("added 'fra-fil'", out.getvalue())
        self.assertIn("replaced 'fra-fil'", out.getvalue())
        self.assertIsNotNone(self.client.get("/api/quizzes/fra-fil").get_json())


if __name__ == "__main__":
    unittest.main()
