# Kviss

A Jeopardy-style quiz built to be run from a phone that is screen-shared to a TV.
It uses Python (Flask), plain HTML and CSS, and needs no JavaScript.
The screens are in Norwegian (bokmål); code, config keys and docs are in English.

- **Board** (`/`): categories, point values, and live scores. Questions that have been played go dark.
- **Question** (`/q/<cat>/<row>`): the question in big text. Tap **Show answer** to reveal the answer to the room.
  Each player has ✓ / ✗ buttons:
  - ✓ adds the question's value and sends you back to the board.
  - ✗ subtracts the value. That player is locked out of the question and the others can still try.
  - **Nobody** closes the question with no change to any score.
- **Admin** (`⚙`, `/admin`): adjust scores by hand, undo, and reset the game.
- **Undo** reverts the last scoring action (up to 50 steps), for when you mis-tap.
- When every question has been played, the board switches to **Final results**.

Game progress is saved in `state.json` after every action. That means a reloaded phone browser or a
restarted server continues the same game. If you edit `quiz.json`, the next start begins a fresh game.

## The quiz file

Edit `quiz.json`, or point `KVISS_CONFIG` at another file:

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

Rules: at least 1 player (names must be unique), at least 1 category, and every question needs a
positive integer `value`, a `question` and an `answer`. Categories can have different numbers of
questions. Missing slots show as blank tiles. Up to about 6 categories × 5 questions reads well on a TV.

The app checks the file at startup and stops with a clear message if something is wrong, for example
`category 'Science', question #3: 'answer' is required`.

### Prompt for generating questions with an AI

> Create a Jeopardy-style quiz as JSON with exactly this structure:
> `{"title": str, "players": [str], "categories": [{"name": str, "questions": [{"value": int, "question": str, "answer": str}]}]}`.
> Make 5 categories about **<TOPICS>**, each with 5 questions valued 100, 200, 300, 400, 500, increasing in difficulty.
> Players: **<NAMES>**. Write each question as a clue, and keep the answer short (1–5 words).
> Language: **Norwegian (bokmål)**. Output only the JSON, with no commentary.

Then check it locally before you deploy:

```bash
python -c "from app import load_quiz; load_quiz('quiz.json'); print('OK')"
```

## Run locally

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py                                   # http://127.0.0.1:8000
HOST=0.0.0.0 .venv/bin/python app.py                      # reachable from your phone on the same Wi-Fi
.venv/bin/python -m unittest discover -s tests -t .      # tests
```

## Deploy to a VPS (systemd + venv + Caddy)

These steps are for Debian 12 (bookworm) or newer, which ships `caddy` in its standard repositories.

```bash
# 1. Code and a service user
sudo apt install -y python3-venv git caddy
sudo useradd --system --home /opt/kviss --shell /usr/sbin/nologin kviss
sudo git clone https://github.com/sonhal/kviss.git /opt/kviss   # private repo: use a deploy key or token
sudo python3 -m venv /opt/kviss/.venv
sudo /opt/kviss/.venv/bin/pip install -r /opt/kviss/requirements.txt
sudo chown -R kviss:kviss /opt/kviss

# 2. Password (strongly recommended: otherwise anyone who finds the URL can change scores)
echo "KVISS_PASSWORD=$(openssl rand -base64 12)" | sudo tee /etc/kviss.env
sudo chmod 600 /etc/kviss.env

# 3. Service
sudo cp /opt/kviss/deploy/kviss.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kviss
sudo systemctl status kviss          # look for "Listening at: http://127.0.0.1:8000"

# 4. HTTPS via Caddy (DNS A/AAAA record for kviss.sonhal.no must point at the VPS; ports 80/443 open)
sudo cp /opt/kviss/deploy/Caddyfile /etc/caddy/Caddyfile   # overwrites the default; merge by hand if Caddy already serves other sites
sudo systemctl reload caddy
```

Open `https://kviss.sonhal.no`. The browser asks for a login: the username can be anything, and the
password is the one in `/etc/kviss.env`.

**Updating the quiz:** edit `quiz.json` (or `git pull`), then run `sudo systemctl restart kviss`.

### Security notes

- Gunicorn only listens on `127.0.0.1`. Caddy is the only thing exposed to the internet, and it terminates TLS.
  Without HTTPS, the Basic Auth password would travel in plain text.
- POSTs whose `Origin` header points at a different site are rejected, which blocks cross-site form
  attacks (CSRF) from other pages open in the same browser.
- All quiz text goes through Jinja's auto-escaping, so HTML in a question cannot inject scripts.
- The systemd unit runs as an unprivileged user with a read-only filesystem except for `/opt/kviss`.
- The app deliberately runs a **single** gunicorn worker. State lives in memory in that one process, so do not raise `--workers`.

## Game-night tips

- Hold the phone in **landscape**. The layout is sized for a 16:9 TV.
- Turn off auto-lock and auto-rotate. Screen-mirroring (AirPlay/Chromecast) mirrors your whole phone,
  so silence notifications (Do Not Disturb).
- Add the page to your home screen to get a fuller screen with no address bar.

## Ideas for after the MVP

- A separate `/host` page on a second device that shows answers privately, while the TV shows `/board`.
- Daily Doubles and a Final Jeopardy round with wagers.
- Upload or switch quiz files from the admin page instead of editing them on the server.
- Sound effects and a countdown timer.
