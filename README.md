# Kviss

A Jeopardy-style quiz for game night. The host runs it from a phone that is screen-shared to the TV and keeps
score as the teams answer. The screens are in Norwegian (bokmål); the code, config and docs are in English.

## Features

- **The classic board.** Categories and point values. The host opens a question, reveals the answer, and taps
  ✓ or ✗ for each team; wrong answers subtract points and lock that team out of the question.
- **Daily Doubles and Final Jeopardy** (*Dagens dobbel*, *Finale*). Both are optional and turned on per game, with
  wagers checked against each team's score and a countdown for the final.
- **Music questions.** Play a clip from YouTube (sound only, so the title doesn't give it away) or from a local
  audio file, with start and end times.
- **A podium at the end.** The host reveals 3rd, 2nd and 1st place one tap at a time, with confetti.
- **A host view** (`/vert`). Open it on a second device to see the answer while the TV shows only the question.
- **Quizzes are stored and reusable.** Write them in the browser builder (`/lag`), upload JSON files (`/last-opp`),
  or use the REST API. Each game keeps its own copy of the quiz, so editing a quiz never changes a running game or
  old results.
- **Nothing is lost on a reload.** Every action is saved to SQLite, so a reloaded phone or restarted server
  picks up the same game. **Angre** (undo) reverts up to 50 steps.
- **History and results.** Every finished game is kept, with a per-question grid of who answered what.
- **Works without JavaScript.** The JS adds animations, an in-place answer reveal and a screen wake lock; the
  game itself is plain forms and links.

[docs/playing.md](docs/playing.md) describes every screen and rule in detail.

## Technologies

| | |
|---|---|
| Backend | Python 3.11+ with [Flask](https://flask.palletsprojects.com), served by gunicorn (one worker, see `app.py`) |
| Validation | [Pydantic](https://docs.pydantic.dev) models in `schemas.py` for quiz JSON and every form post |
| Storage | SQLite (`kviss.db`) through the standard library, with parameterised SQL |
| Frontend | Jinja templates, plain CSS, and vanilla JavaScript (`static/app.js` for polish, `static/builder.js` for the quiz builder) |
| Packaging | Docker image on `ghcr.io/sonhal/kviss`, or systemd + venv behind Caddy |
| CI | GitHub Actions: ruff, hadolint, shellcheck, unit tests on 3.11 and 3.12, Docker build, automatic releases |

## Project layout

```
app.py              the whole web app: routes, game logic, SQLite store, `python app.py import` CLI
schemas.py          Pydantic models for quiz files and form input
templates/          Jinja templates, one per screen (partials start with _)
static/             CSS, JavaScript, icons, PWA manifest, and the downloadable quiz template
tests/test_app.py   unit tests (unittest + Flask's test client)
quiz.json           quizzes imported into an empty database on first start
quiz-example.json
deploy/             systemd unit and Caddy site block for a VPS
scripts/            next-version.sh, which decides the next release from commit subjects
docs/               detailed documentation (see below)
```

## Getting started

You need Python 3.11 or newer.

```bash
git clone https://github.com/sonhal/kviss.git && cd kviss
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                     # http://127.0.0.1:8000, with Flask's reloader
```

With no `KVISS_PASSWORD` set, the app doesn't ask for a password. On first start, the empty database
(`kviss.db`) imports `quiz.json` and `quiz-example.json`, so there is something to play right away. To try it on a
phone on the same Wi-Fi, run `HOST=0.0.0.0 .venv/bin/python app.py` and open the computer's IP address on port 8000.

Or run it in Docker, the same way as in production:

```bash
echo "KVISS_PASSWORD=dev" > .env
docker compose up -d --build                # http://127.0.0.1:8000, any username, password "dev"
```

### Checks

CI runs these on every pull request; run them before you push:

```bash
pip install ruff && ruff check .
.venv/bin/python -m unittest discover -s tests -t .
```

A `Dockerfile` change should also build and start with `docker compose up -d --build`.

### Contributing

Pull requests are squash-merged, and the PR title becomes the commit subject on `main`. Use
[Conventional Commits](https://www.conventionalcommits.org): `feat:` cuts a minor release, `fix:` a patch, and
`docs:`, `ci:`, `chore:`, `refactor:` and `test:` cut none. See [docs/ci-and-releases.md](docs/ci-and-releases.md).

## Documentation

- [docs/playing.md](docs/playing.md): every screen, the Daily Double and Final rules, and game-night tips
- [docs/quizzes.md](docs/quizzes.md): the builder, uploads, the API, the quiz JSON format, music questions, and an
  AI prompt for writing quizzes
- [docs/deploy.md](docs/deploy.md): Docker, released images, a systemd + Caddy VPS, settings, and backups
- [docs/security.md](docs/security.md): how the app is protected, and what to keep in mind when hosting it
- [docs/ci-and-releases.md](docs/ci-and-releases.md): the CI pipeline and automatic releases
