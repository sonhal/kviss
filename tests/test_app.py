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

    def test_bad_config_message(self):
        self.config.write_text(json.dumps({**QUIZ, "players": []}))
        with self.assertRaisesRegex(ConfigError, "players"):
            load_quiz(self.config)


if __name__ == "__main__":
    unittest.main()
