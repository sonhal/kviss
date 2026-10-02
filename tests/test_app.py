import base64
import json
import tempfile
import unittest
from pathlib import Path

from app import BASE_DIR, ConfigError, create_app, load_quiz

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
        self.config = self.tmp / "quiz.json"
        self.config.write_text(json.dumps(QUIZ))
        self.state = self.tmp / "state.json"
        self.client = self.make_client()

    def make_client(self, password=""):
        app = create_app(self.config, self.state, password=password)
        self.game = app.config["GAME"]
        return app.test_client()

    def judge(self, c, r, result, player=None):
        data = {"result": result}
        if player is not None:
            data["player"] = str(player)
        return self.client.post(f"/q/{c}/{r}/judge", data=data)

    def test_example_quiz_is_valid(self):
        load_quiz(BASE_DIR / "quiz.json")

    def test_board_renders_and_escapes(self):
        self.assertEqual(self.client.get("/").status_code, 200)
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
        page = self.client.get("/").get_data(as_text=True)
        self.assertIn("step slot-1 rank-1", page)
        self.assertNotIn('class="scores"', page)

    def test_classic_scoring(self):
        self.judge(0, 1, "wrong", 0)
        self.assertEqual(self.game.state["scores"], [-200, 0])
        self.judge(0, 1, "wrong", 0)  # same player can't answer twice
        self.assertEqual(self.game.state["scores"], [-200, 0])
        resp = self.judge(0, 1, "correct", 1)
        self.assertEqual(resp.headers["Location"], "/")
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
        self.assertIn("Sluttresultat", self.client.get("/").get_data(as_text=True))

    def test_state_survives_restart_but_not_quiz_change(self):
        self.judge(0, 0, "correct", 1)
        self.make_client()
        self.assertEqual(self.game.state["scores"], [0, 100])
        self.config.write_text(json.dumps({**QUIZ, "title": "Changed"}))
        self.make_client()
        self.assertEqual(self.game.state["scores"], [0, 0])

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
        self.assertIn('href="/regler"', self.client.get("/").get_data(as_text=True))

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
        self.client.get("/")  # TV back on the board
        self.assertIn("Brettet vises", self.client.get("/vert/panel").get_data(as_text=True))

    def test_bad_config_message(self):
        self.config.write_text(json.dumps({**QUIZ, "players": []}))
        with self.assertRaisesRegex(ConfigError, "players"):
            load_quiz(self.config)

    # --- music questions ---------------------------------------------------

    def music_quiz(self, *extra):
        """Write a quiz whose first category has the given music questions; return a client."""
        (self.tmp / "media").mkdir(exist_ok=True)
        (self.tmp / "media" / "song.mp3").write_bytes(b"ID3" + bytes(range(256)) * 4)
        quiz = json.loads(json.dumps(QUIZ))
        for q in extra:
            quiz["categories"][0]["questions"].append({"value": 300, "question": "Låt?", "answer": "Svar", **q})
        self.config.write_text(json.dumps(quiz))
        return self.make_client()

    def assert_bad_music(self, fields, message):
        with self.assertRaisesRegex(ConfigError, message):
            self.music_quiz(fields)

    def test_music_validation(self):
        self.music_quiz({"youtube": "dQw4w9WgXcQ", "start": 30, "end": 45.5}, {"audio": "song.mp3"})
        self.assert_bad_music({"youtube": "https://youtu.be/dQw4w9WgXcQ"}, "11-character video ID")
        self.assert_bad_music({"youtube": "dQw4w9WgXc\"><x"}, "11-character video ID")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "audio": "song.mp3"}, "not both")
        self.assert_bad_music({"audio": "missing.mp3"}, "not found")
        self.assert_bad_music({"audio": "../quiz.json"}, "must be one of")
        self.assert_bad_music({"audio": "../media/song.mp3"}, "inside")
        self.assert_bad_music({"audio": "/etc/song.mp3"}, "inside")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": 20, "end": 10}, "'end'")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": -1}, "'start'")
        self.assert_bad_music({"youtube": "dQw4w9WgXcQ", "start": True}, "'start'")
        self.assert_bad_music({"start": 10}, "needs 'youtube' or 'audio'")

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
        (self.tmp / "media" / "other.mp3").write_bytes(b"x")
        self.assertEqual(client.get("/media/other.mp3").status_code, 404)
        self.assertEqual(client.get("/media/../quiz.json").status_code, 404)

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


if __name__ == "__main__":
    unittest.main()
