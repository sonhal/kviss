# Kviss

A Jeopardy-style quiz built to be run from a phone that is screen-shared to a TV.
It uses Python (Flask), HTML and CSS, with a small JavaScript layer (`static/app.js`) for polish.
Everything still works if that script fails to load.
The screens are in Norwegian (bokmål); code, config keys and docs are in English.

Quizzes are stored in a SQLite database (`kviss.db`) and can be played again and again with different players.
You add quizzes by uploading JSON files on **Last opp kviss** (`/last-opp`) or through the API (see
[Managing quizzes](#managing-quizzes)), and pick one on **Nytt spill** before each game.

- **Landing page** (`/`, where the home-screen icon opens): the game on the TV right now, with its standings and a
  **Fortsett** button back to the board, a **Nytt spill** button, and **Tidligere spill**, the last 50 finished
  games with their date, winner and how far they got. Pages that need a game send you here when none is running.
- **Nytt spill** (`/nytt`): pick a stored quiz, then name the game and type the teams or players, one per line.
  The name is pre-filled as "quiz title · date" and shows on the TV, the host view, the landing page and the
  results. Leave it empty to name the game after the quiz. The list shows how
  many times each quiz has been played and when. Starting a game ends the one on the TV. If that game was
  half-way through you have to tick a box first. A game where nothing was scored is just dropped. The others are
  kept in the history. When the podium is showing, the top bar gets a **Nytt spill** button.
- **Last opp kviss** (`/last-opp`, linked from the landing page, **Nytt spill** and admin): upload one or more quiz
  JSON files from the browser. The page explains the format in Norwegian, with a downloadable template
  (`static/kviss-mal.json`) and a prompt for making questions with an AI. Each file is checked like an API upload:
  a valid one is saved (or replaces the quiz with the same slug) and links straight to starting a game, a broken
  one is listed with every problem and changes nothing.
- **Board** (`/brett`): categories, point values, and live scores. Questions that have been played go dark.
- **Question** (`/q/<cat>/<row>`): the question in big text. Tap **Show answer** to reveal the answer to the room.
  Each player has ✓ / ✗ buttons:
  - ✓ adds the question's value and sends you back to the board.
  - ✗ subtracts the value. That player is locked out of the question and the others can still try.
  - **Nobody** closes the question with no change to any score.
- **Dagens dobbel** (Daily Double, optional): tick **Med Dagens dobbel** on **Nytt spill** and choose how many
  (1–10). When the game starts, the app hides them behind random tiles, in different categories as on the TV show.
  Nobody knows where they are, not even the host: the board, the host view and the admin page look the same as
  in any other game. Opening one shows **DAGENS DOBBEL!** instead of the question. The host taps the team that
  picked the tile and types its bet: at least 100 (less on a board with smaller values), and at most the team's
  whole score, or the board's highest value if the score is lower than that. Then the question appears, and only
  that team answers: ✓ adds the bet,
  ✗ subtracts it and ends the question. **Angre** takes back the answer, and then the bet. Results pages mark the
  Daily Doubles that were found, and every one once the game has ended. **Nullstill spill** hides them on new
  tiles. The music check on the admin page (`?test=1`) never shows a Daily Double and can't score.
- **Finale** (Final Jeopardy, for quizzes with a [`final`](#the-quiz-format) question): ticked by default on
  **Nytt spill**, where you also set the countdown (30 seconds by default; the next game suggests the time you
  used last). When the board is empty, the TV shows the final's category instead of the podium, and which teams
  play: everyone above 0 points. Teams write their bet (0 up to their whole score) and their answer on paper.
  **Vis spørsmålet** shows the question with the countdown, and **Avslør svarene** shows the answer. Then the
  teams are revealed one at a time, lowest score first so the leader comes last: the host types the team's bet
  and taps ✓ (adds it) or ✗ (subtracts it). Every result stays on the TV until **Se sluttresultat** goes on to the
  podium. If nobody is above 0, the final is skipped. The countdown carries on if the page is reloaded, the host view shows the answer, **Angre**
  steps back one reveal at a time, and the results page lists each team's bet.
- **Resultat** (`/resultat/<id>`): one game's final ranking, when it started and ended, and a grid of every
  question showing who answered it right (✓), wrong (✗), nobody, or that it was never played. Opened from the
  landing page or the admin history.
- **Regler** (`/regler`): a one-screen summary of the rules in Norwegian for the contestants, linked from the top bar.
- **Vertsvisning** (`/vert`): open this on a **second device** to see the answer while you host. It follows the TV
  live, updating about a second after you open a question. It shows the question, the answer in large text, who
  has answered wrong and the scores. It is read-only: it has no buttons and can't change the game.
  Log in with the same password. The link is also on the admin page.
- **Music questions**: the question screen gets **▶ Spill av** / **❚❚ Pause** and **↺ Fra start** buttons
  (the space bar also plays and pauses) and plays a clip from a YouTube video or a local audio file. The YouTube
  video is never shown, only heard: the player is invisible because its title is often the answer.
  See [Music questions](#music-questions).
- **Admin** (`⚙`, `/admin`): start a new game, adjust scores by hand, undo, and reset the game. It also lists
  every music question, so you can test that each clip plays before the game. **Historikk** lists the last 20
  games with their quiz, date and final standings, each linking to its results page.
- **Undo** reverts the last scoring action (up to 50 steps), for when you mis-tap.
- When every question has been played, the board switches to a **Kahoot-style podium**. **You control the
  reveal:** tap the screen (or the pulsing **Avslør …** button) to raise 3rd place, tap again for 2nd, and once
  more for the winner and confetti. Places 4+ appear under the podium at the end. Tied teams share a place.
  Reload the page to replay the reveal, and use **Angre** if the last question was judged wrong.
  (Without JavaScript, the podium plays the same reveal automatically.)

### Polish (JavaScript and modern CSS, all optional)

- The tapped tile zooms into the question screen. This uses cross-document View Transitions, available in
  Chrome 126+ and Safari 18.2+; other browsers just navigate normally.
- **Vis svar** reveals the answer in place, without a page reload.
- Scores count up or down to their new value and flash green or red on the board.
- The phone screen is kept awake while the page is open (Wake Lock API, which needs HTTPS).
- Judge buttons ignore double taps and vibrate briefly on Android.
- *Reduce motion* in the phone's accessibility settings turns the animations off.

Game progress is saved in the database after every action. That means a reloaded phone browser or a
restarted server continues the same game. Each game keeps its own copy of the quiz as it was when the game
started. Replacing or deleting a quiz therefore never changes a running game or old results. Start a new game to
play the new version.

## Managing quizzes

A quiz is a JSON document (format below). The first time the app starts with an empty database, it imports
`quiz.json` (or the file `KVISS_CONFIG` points at) and `quiz-example.json`, a full 5-player quiz (Politikk,
Sport, Musikk, Underholdning, Godt og Blandet). After that, the database is the only source. Editing
`quiz.json` does nothing until you upload it again.

Each quiz has a **slug**, a short name like `fredagskviss`. It is made from the title (`"Fredagskviss på Bærum"`
→ `fredagskviss-pa-baerum`), or you can set it yourself with a `"slug"` field. **Uploading a quiz with a slug that
already exists replaces it.** That is how you fix a typo. To keep both, give the new one another title or slug.

### Uploading from the browser

Open **Last opp kviss** (`/last-opp`), pick one or more `.json` files and tap **Last opp**. The checks, the
1 MB limit (for all the files together) and the replace-by-slug rule are the same as for the API below. Audio files
for music questions still have to be copied to the server first.

### The API

All endpoints use the same password as the rest of the app (any username). Examples with `curl`:

```bash
PW='your password from /etc/kviss.env'
URL=https://kviss.sonhal.no

# Add a quiz, or replace the one with the same slug. 201 = added, 200 = replaced, 400 = what's wrong.
curl -u ":$PW" -H 'Content-Type: application/json' --data-binary @fredagskviss.json "$URL/api/quizzes"

curl -u ":$PW" "$URL/api/quizzes"                         # list: slug, title, size, times played
curl -u ":$PW" "$URL/api/quizzes/fredagskviss" > f.json   # download, edit, upload again
curl -u ":$PW" -X DELETE "$URL/api/quizzes/fredagskviss"  # 204; old games keep their copy
```

A rejected upload changes nothing and lists every problem at once, for example
`{"error": "...", "problems": ["category 'Sport', question #3, 'answer' is required", "category 'Sport', question #4, 'value' must be a whole number"]}`
(`error` is the same list as one string, one problem per line). Uploads over 1 MB get `413`.

You can also import files from a shell on the server. Run it as the `kviss` user so the database keeps the right
owner:

```bash
sudo -u kviss /opt/kviss/.venv/bin/python /opt/kviss/app.py import /tmp/fredagskviss.json   # locally: .venv/bin/python app.py import x.json
```

### The quiz format

```json
{
  "title": "Fredagskviss",
  "players": ["Lag Rød", "Lag Blå"],
  "categories": [
    {
      "name": "Geografi",
      "questions": [
        { "value": 100, "question": "Dette er hovedstaden i Norge.", "answer": "Oslo" },
        { "value": 200, "question": "...", "answer": "..." }
      ]
    }
  ]
}
```

Rules: a `title`, at least 1 category, and every question needs a positive integer `value`, a `question` and an
`answer`. `players` is optional. It only pre-fills the names on the new-game screen, and you can change them
there. If it's left out, the names from the last game are pre-filled instead. `slug` is optional (see above). A question can also play a song: see
[Music questions](#music-questions). `final` is optional: a Final Jeopardy question with a `category`, a
`question` and an `answer`, played after the board (see **Finale** above), e.g.
`"final": { "category": "Norsk historie", "question": "I dette året ble unionen med Sverige oppløst.", "answer": "1905" }`. Categories can have different numbers of questions. Missing slots show
as blank tiles. Up to about 6 categories × 5 questions reads well on a TV.

The app checks the quiz when you upload it and rejects it with a clear message if something is wrong, for
example `category 'Science', question #3, 'answer' is required`. The checks are strict, to catch mistakes early:

- Values need the right JSON type: `"value": 100`, not `"value": "100"`, and `"start": 30`, not `"start": "30"`.
- Unknown fields are rejected, so a typo like `"anwser"` is reported instead of silently ignored.
- Spaces around text are trimmed, and text can't be empty.
- Limits: at most 12 categories, 20 questions per category and 30 players; titles and category names up to
  100 characters, questions and answers up to 1000, player names up to 40.

The rules are in `schemas.py` (Pydantic models).

### Music questions

A music question plays a song clip, and the players answer a question about it (title, artist, year …).
One clip is one question, and the host judges it like any other. The song comes either from **YouTube**
(only the sound is played, the video is never shown) or from an **audio file** such as an MP3.

#### Add a YouTube song

1. Find the song on YouTube and copy the link, e.g. `https://www.youtube.com/watch?v=dQw4w9WgXcQ`.
2. Take the **video ID**: the 11 characters after `v=` (here `dQw4w9WgXcQ`). In a share link like
   `https://youtu.be/dQw4w9WgXcQ?si=…` it is the part after `youtu.be/` and before `?`.
   Paste only the ID, not the whole link.
3. Pick the part of the song to play. Find the start time in the YouTube player, e.g. 1:15 = 75 seconds.
4. Add the question to a category in `quiz.json`:

   ```json
   { "value": 300, "question": "Hva heter låta?", "answer": "Never Gonna Give You Up",
     "youtube": "dQw4w9WgXcQ", "start": 75, "end": 90 }
   ```

5. Upload the quiz again ([The API](#the-api)), start a game with it, open **⚙ Admin → Musikk** and tap the
   question to check that the clip plays. Some videos can't be played outside YouTube (see below). If so,
   use another upload of the same song.

#### Add an MP3 (or other audio file)

1. Put the file in the `media` folder next to `app.py` (or the folder `KVISS_MEDIA` points at).
   Locally: `kviss/media/take-on-me.mp3`. On the server: `/opt/kviss/media/take-on-me.mp3`.
   Sub-folders are fine. Formats: `.mp3`, `.m4a`, `.aac`, `.wav`.
2. Add the question, with the file name relative to `media/`:

   ```json
   { "value": 400, "question": "Hvem er artisten?", "answer": "a-ha",
     "audio": "take-on-me.mp3", "start": 0, "end": 20 }
   ```

3. Copy the files to the server **before you upload the quiz**: the upload is rejected if a file it names is
   missing. `media/` is in `.gitignore`, so `git pull` does not bring the songs:

   ```bash
   scp media/*.mp3 you@your-vps:/tmp/                    # from your own machine
   sudo mkdir -p /opt/kviss/media                        # on the server
   sudo mv /tmp/*.mp3 /opt/kviss/media/
   sudo chown -R kviss:kviss /opt/kviss/media
   ```

4. Upload the quiz, start a game with it, and test the clip from **⚙ Admin → Musikk**.

#### Fields

- `youtube`: an 11-character YouTube **video ID**. A whole link is rejected.
- `audio`: a file name inside `media/`, e.g. `"take-on-me.mp3"` or `"80s/take-on-me.mp3"`.
- Use **either** `youtube` **or** `audio` in a question, not both.
- `start` / `end` (optional): seconds into the song. Playback begins at `start` (default 0) and stops at
  `end` (default: the end of the song). **Spill av** after the clip has ended, or **Fra start**, plays it again
  from `start`. Decimals like `42.5` are allowed.
- `question`, `answer` and `value` work as for any other question.

The upload checks music questions like the rest of the quiz. A missing audio file, a link instead of an ID, or
`end` before `start` is rejected with a clear message, e.g.
`category 'Musikk', question #3: audio file not found: /opt/kviss/media/take-on-me.mp3`. The host view (`/vert`)
shows which clip is playing, but never plays sound itself.

Things to know about YouTube:

- Some videos, often official label/VEVO uploads, **can't be played outside YouTube**. The question screen
  then says so ("Eieren tillater ikke …"). Test every clip from the list on the admin page before the game,
  and pick another upload (e.g. a "lyrics" or "topic" video) if one fails.
- Monetised videos can play an **ad** before the song, which the room will hear. Being logged in to
  YouTube Premium in that browser avoids it. Local files have no ads and are the most reliable option.
- YouTube's API terms do not allow an invisible player. That is the trade-off for never showing the title.
- Music needs JavaScript. Without it, an audio file falls back to the browser's own player (starting at
  `start`), and a YouTube question shows an "Åpne på YouTube" link that *does* show the video.

### Prompt for generating questions with an AI

> Create a Jeopardy-style quiz as JSON with exactly this structure:
> `{"title": str, "players": [str], "categories": [{"name": str, "questions": [{"value": int, "question": str, "answer": str}]}], "final": {"category": str, "question": str, "answer": str}}`.
> Make 5 categories about **<TOPICS>**, each with 5 questions valued 100, 200, 300, 400, 500, increasing in difficulty.
> `final` is one extra, hard Final Jeopardy question in its own category.
> Players: **<NAMES>**. Write each question as a clue, and keep the answer short (1–5 words).
> Language: **Norwegian (bokmål)**. Output only the JSON, with no commentary.

An AI often makes up YouTube IDs that don't exist, so add music questions yourself (see
[Music questions](#music-questions)) and test them on the admin page.

Leave out `players` if you'd rather type them on game night. To check a file locally without uploading it:

```bash
.venv/bin/python -c "from app import load_quiz; load_quiz('my-quiz.json'); print('OK')"
```

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                                   # http://127.0.0.1:8000
HOST=0.0.0.0 .venv/bin/python app.py                      # reachable from your phone on the same Wi-Fi
.venv/bin/python -m unittest discover -s tests -t .      # tests
```

## Run with Docker

The image holds the app only. Everything that changes lives in **`/data`** inside the container, so mount a
volume there:

- `/data/kviss.db` is the database, with `kviss.db-wal` / `kviss.db-shm` next to it while the app runs. Mount the
  **folder**, not just the `.db` file, because SQLite creates those extra files beside the database.
- `/data/media/` holds audio files for music questions.

Use the same environment variables as for systemd (`KVISS_PASSWORD`, `KVISS_TZ`, …). `KVISS_DB` and `KVISS_MEDIA`
are already set to the paths above. On first start, an empty database imports `quiz.json` and `quiz-example.json`
from the image, as described in [Managing quizzes](#managing-quizzes).

### With Docker Compose

```bash
echo "KVISS_PASSWORD=$(openssl rand -base64 12)" > .env   # compose reads .env; it is in .gitignore
docker compose up -d --build                              # http://127.0.0.1:8000
docker compose logs -f
```

`compose.yaml` stores `/data` in a named volume, `kviss-data`, which survives `docker compose down`, rebuilds and
image updates. Only `docker compose down -v` deletes it. The port is published on `127.0.0.1` only. Put Caddy in
front for HTTPS: the site block in `deploy/kviss.caddy` works unchanged. Don't run the systemd service and the
container at the same time, because both use port 8000.

**Updating:** `git pull && docker compose up -d --build`. The volume, and with it every quiz and game, is kept.

### With plain `docker run`

```bash
docker build -t kviss .
docker run -d --name kviss --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -e KVISS_PASSWORD='your password' \
  -v kviss-data:/data kviss
```

### Using a released image

Every release is published to the GitHub Container Registry for `linux/amd64` and `linux/arm64`, so you can skip
the build:

```bash
docker pull ghcr.io/sonhal/kviss:0.1.0     # a fixed version (also :0.1 for the newest 0.1.x, and :latest)
docker run -d --name kviss --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -e KVISS_PASSWORD='your password' \
  -v kviss-data:/data ghcr.io/sonhal/kviss:0.1.0
```

With Compose, replace `build: .` and `image: kviss` in `compose.yaml` with `image: ghcr.io/sonhal/kviss:0.1.0`,
and update with `docker compose pull && docker compose up -d`. Pin a version rather than `latest`, so an update
happens when you change the tag and not by surprise. To check that an image was built by this repository's CI:
`gh attestation verify oci://ghcr.io/sonhal/kviss:0.1.0 --repo sonhal/kviss`.

### Keeping the data in a folder on the host

A **bind mount** is easier to back up and to copy audio files into than a named volume. The app runs as the
unprivileged user `kviss` with **UID/GID 1000** inside the container, so that user must own the folder.
Otherwise it stops with `sqlite3.OperationalError: unable to open database file`.

```bash
sudo mkdir -p /srv/kviss/media
sudo chown -R 1000:1000 /srv/kviss
docker run -d --name kviss --restart unless-stopped \
  -p 127.0.0.1:8000:8000 -e KVISS_PASSWORD='your password' \
  -v /srv/kviss:/data kviss
```

In `compose.yaml`, change `- kviss-data:/data` to `- /srv/kviss:/data`. To reuse the database from a systemd
install, stop that service, copy `kviss.db` into the folder (with `kviss.db-wal`/`-shm` if they exist), and `chown`
it as above. Add audio files with `sudo cp song.mp3 /srv/kviss/media/ && sudo chown 1000:1000 /srv/kviss/media/song.mp3`,
or for a named volume use `docker cp song.mp3 kviss:/data/media/`.

### Shell commands in the container

```bash
# The file is piped in, because compose.yaml makes the container's filesystem read-only (docker cp to /tmp fails)
docker compose exec -T kviss python app.py import /dev/stdin < fredagskviss.json   # plain docker: docker exec -i kviss ...

# consistent backup while it runs, written into the volume
docker exec kviss python -c "import sqlite3; sqlite3.connect('/data/kviss.db').backup(sqlite3.connect('/data/backup.db'))"
docker cp kviss:/data/backup.db .
```

### Differences from the systemd setup

Mostly things that behave the same but are configured somewhere else:

- **Time zones.** Dates in the app follow `KVISS_TZ` (default `Europe/Oslo`), not the server's or the container's
  clock zone. The database stores UTC, so moving between systemd and Docker never shifts a date. A misspelled
  zone (`Europe/Olso`) falls back to UTC with a warning in the log, so check the log if times are off by an hour or
  two. The container's own clock is UTC unless `TZ` is set. `compose.yaml` sets `TZ` to the same zone, so the
  timestamps in `docker compose logs` match (with plain `docker run`, add `-e TZ=Europe/Oslo`). The image has its own
  copy of the time zone rules, which is updated when you rebuild it (`docker compose build --pull`), not by
  `apt upgrade` on the host.
- **Firewall.** A port published by Docker skips ufw/firewalld. Keep the `127.0.0.1:` in front of the port, or the
  app is reachable over plain HTTP from the internet, whatever ufw says.
- **Logs** are in `docker compose logs` instead of `journalctl -u kviss`. Docker never deletes old logs unless told
  to, so `compose.yaml` keeps 3 × 10 MB. Add the same `--log-opt max-size=10m --log-opt max-file=3` to a plain
  `docker run`. As with systemd, requests are not logged: the host view polls every 1.5 s.
- **Hardening.** The systemd unit makes everything but `/opt/kviss` read-only. `compose.yaml` does the same with a
  read-only root filesystem (only `/data` and an in-memory `/tmp` can be written), no Linux capabilities, and
  `no-new-privileges`.
- **Restarts.** `restart: unless-stopped` restarts the app if it crashes and after a reboot (if Docker starts at
  boot: `systemctl is-enabled docker`). An `unhealthy` health check does **not** restart it, it only shows in
  `docker ps`.
- **Stopping.** `docker stop` waits 10 seconds, then kills the app. Every save is a single SQLite transaction, so
  this can't corrupt the database. At worst the tap made during the stop is lost.
- **Updates** come from rebuilding the image, not from `pip install` in a venv. `docker compose build --pull`
  also picks up Python and Debian security fixes. The image runs Python 3.12 on Debian 13, where a Debian 12 VPS
  runs 3.11. The tests pass on both.
- **Files the app reads** must be inside the container. `KVISS_CONFIG` and audio files must be under `/data`;
  host paths like `/opt/kviss/media` mean nothing in the container. `quiz.json` and `quiz-example.json` come from
  the image, and are only read when the database is empty.
- **Docker Desktop (Mac/Windows).** On a laptop, keep `/data` in a named volume. SQLite's locking and WAL files
  can misbehave on folders shared from the host OS (especially `C:\` under Windows). On a Linux VPS, both kinds of
  mount are fine.

The container's health check counts any HTTP answer, including `401` from the password prompt, as healthy
(`docker ps` shows `healthy`). The same single-gunicorn-worker rule from the security notes applies: run **one**
container per database, and don't scale the service.

## CI and releases

`.github/workflows/ci.yml` runs on every pull request and push to `main`:

- **Lint:** `ruff check` (settings in `ruff.toml`), hadolint on the `Dockerfile`, shellcheck on `scripts/`.
- **Test:** the unit tests on Python 3.11 (Debian 12, the systemd deploy) and 3.12 (the Docker image).
- **Docker build:** builds the image and starts it with the same hardening as `compose.yaml` (read-only root
  filesystem, no capabilities), then checks that it gets healthy, asks for the password and serves the start page.

**Releases are cut automatically** from `main`. After a push to `main` passes every job, the Tag release job runs
`scripts/next-version.sh`, which reads the [Conventional Commits](https://www.conventionalcommits.org) subjects of
the commits since the latest `vX.Y.Z` tag and takes the largest bump:

| Commit subject | Bump |
|---|---|
| `feat: ...` / `feat(admin): ...` | minor |
| `fix: ...`, `perf: ...` | patch |
| `feat!: ...`, or a `BREAKING CHANGE:` footer in the body | major (minor while the version is `0.x`) |
| anything else (`docs:`, `ci:`, `chore:`, `refactor:`, `test:`, a plain subject) | no release |

If there is a bump, it tags the commit and runs the pipeline again on the tag. That run's Release job pushes the
image to `ghcr.io/sonhal/kviss` (tags `X.Y.Z`, `X.Y`, `latest`, and `X` from 1.0 on) with a signed build provenance
attestation, and creates a GitHub Release with generated notes. Pull requests are squash-merged, so **the PR title
is the commit subject** that decides the release.

- `scripts/next-version.sh` prints what the current branch would release (the reasoning goes to stderr).
- To release by hand, push a tag: `git tag -a v0.3.0 -m v0.3.0 && git push origin v0.3.0`. A tag with a hyphen
  (`v0.3.0-rc.1`) becomes a pre-release, which doesn't move `latest`. Leaving `0.x` is deliberate: push `v1.0.0`
  by hand.
- The systemd deploy can follow releases too: `git checkout v0.3.0` instead of `git pull`.
- Dependabot opens weekly PRs for the Python packages, the base image and the GitHub Actions. Their `build(deps):`
  titles don't cut a release on their own.

## Deploy to a VPS (systemd + venv + Caddy)

These steps are for Debian 12 (bookworm) or newer, on a VPS that already runs Caddy for other sites.
Nothing here replaces your existing Caddyfile: kviss is added as one imported site block.

```bash
# 1. Code and a service user
sudo apt install -y python3-venv git
sudo useradd --system --home /opt/kviss --shell /usr/sbin/nologin kviss
sudo git clone https://github.com/sonhal/kviss.git /opt/kviss   # private repo: use a deploy key or token
sudo python3 -m venv /opt/kviss/.venv
sudo /opt/kviss/.venv/bin/pip install -r /opt/kviss/requirements.txt
sudo chown -R kviss:kviss /opt/kviss

# 2. Password (strongly recommended: otherwise anyone who finds the URL can change scores)
echo "KVISS_PASSWORD=$(openssl rand -base64 12)" | sudo tee /etc/kviss.env
sudo chmod 600 /etc/kviss.env

# 3. Service. First check that nothing else already listens on port 8000.
#    If something does, change 8000 in deploy/kviss.service AND deploy/kviss.caddy.
sudo ss -ltnp | grep ':8000 ' || echo "port 8000 is free"
sudo cp /opt/kviss/deploy/kviss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kviss
sudo systemctl status kviss          # look for "Listening at: http://127.0.0.1:8000"
curl -I http://127.0.0.1:8000/       # expect 401 (password set) or 200

# 4. HTTPS via your existing Caddy (DNS A/AAAA record for kviss.sonhal.no must point at the VPS)
sudo cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak                 # backup
sudo cp /opt/kviss/deploy/kviss.caddy /etc/caddy/kviss.caddy
echo 'import /etc/caddy/kviss.caddy' | sudo tee -a /etc/caddy/Caddyfile   # appends after your other sites
sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile     # must say "Valid configuration"
sudo systemctl reload caddy          # graceful: your other sites keep serving
```

The `import` line has to come after any global options block (`{ ... }` at the very top of the
Caddyfile). Appending it to the end of the file satisfies that. If `validate` fails, nothing has been
applied yet. Fix the problem, or restore the backup, before you reload.

If you keep site blocks in a directory you already import (e.g. `import sites/*`), copy
`kviss.caddy` into that directory instead and skip the `echo ... | tee -a` line.

Open `https://kviss.sonhal.no`. The browser asks for a login: the username can be anything, and the
password is the one in `/etc/kviss.env`.

**Adding or changing quizzes:** upload them through the API ([Managing quizzes](#managing-quizzes)). No restart
is needed. Audio files for music questions are not in git: copy them to `/opt/kviss/media/` as described in
[Add an MP3](#add-an-mp3-or-other-audio-file).

**Updating the app:** `cd /opt/kviss && sudo git pull && sudo /opt/kviss/.venv/bin/pip install -r requirements.txt && sudo chown -R kviss:kviss /opt/kviss && sudo systemctl restart kviss`.
The `pip install` step picks up new dependencies (such as Pydantic); skipping it can stop the app from starting.

**Upgrading from the version with `state.json`:** after the pull and restart, the empty database imports
`quiz.json` and `quiz-example.json`. Open the site, which goes to **Nytt spill**, pick a quiz and type the players.
A game in progress in the old `state.json` is not carried over. Delete the file once you're done with it.

**The database** is `/opt/kviss/kviss.db`, plus `kviss.db-wal` and `kviss.db-shm` while the app runs (SQLite's
write-ahead log). Copy all three, or make a consistent copy while it runs:

```bash
sudo -u kviss /opt/kviss/.venv/bin/python -c "import sqlite3; sqlite3.connect('/opt/kviss/kviss.db').backup(sqlite3.connect('/opt/kviss/backup.db'))"
```

Settings in `/etc/kviss.env` (all optional): `KVISS_PASSWORD`, `KVISS_DB` (database path), `KVISS_MEDIA` (audio
folder), `KVISS_CONFIG` (quiz imported on first start) and `KVISS_TZ` (time zone for dates, default `Europe/Oslo`).

### Security notes

- Gunicorn only listens on `127.0.0.1`. Caddy is the only thing exposed to the internet, and it terminates TLS.
  Without HTTPS, the Basic Auth password would travel in plain text.
- POSTs whose `Origin` header points at a different site are rejected, which blocks cross-site form
  attacks (CSRF) from other pages open in the same browser.
- The quiz API and the upload page need the password like every other page. Request bodies over 1 MB are refused (`413`). Further
  protection of `/api/` (rate limits, IP allow-lists) belongs in the Caddy config in front of the app.
- All input is parsed by Pydantic models in `schemas.py` before the app uses it. An upload is checked in full
  before anything is written. Quiz JSON is checked strictly (no type coercion, no unknown fields, no
  `NaN`/`Infinity`, size limits). Every form post is checked too (player index, score change, verdict, redirect
  target); a value the pages never send gets a `400`.
- The database is only reached through parameterised SQL queries, so quiz text can't alter a query.
- All quiz text goes through Jinja's auto-escaping, so HTML in a question cannot inject scripts.
- A `youtube` value must be exactly an 11-character ID (`A-Z a-z 0-9 _ -`), so the quiz file can't point the
  player at anything else. `/media/` serves only the audio files the current game's quiz names, from inside the `media/`
  folder, and is behind the same password as everything else.
- The systemd unit runs as an unprivileged user with a read-only filesystem except for `/opt/kviss`.
- The app deliberately runs a **single** gunicorn worker. The current game is also held in memory in that one
  process, along with the question the TV shows for the host view, so do not raise `--workers`.

## Game-night tips

- Hold the phone in **landscape**. The layout is sized for a 16:9 TV.
- Turn off auto-lock and auto-rotate. Screen-mirroring (AirPlay/Chromecast) mirrors your whole phone,
  so silence notifications (Do Not Disturb).
- **Sound on the TV:** AirPlay mirroring from an **iPhone** sends the sound to the TV automatically. From a
  **Mac** (AirPlay or HDMI), check that the TV is selected as the sound output (Control Center → Sound) and
  that its volume is up. Play a clip from the admin list before the guests arrive.
- **Add to Home Screen** and open Kviss from that icon. It then runs full screen in landscape, with no address bar on the TV.

## Ideas for after the MVP

- Daily Doubles and a Final Jeopardy round with wagers.
- A quiz editor in the browser instead of uploading JSON.
- Sound effects and a countdown timer.
