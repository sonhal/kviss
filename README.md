# Kviss

A Jeopardy-style quiz built to be run from a phone that is screen-shared to a TV.
It uses Python (Flask), HTML and CSS, with a small JavaScript layer (`static/app.js`) for polish.
Everything still works if that script fails to load.
The screens are in Norwegian (bokmål); code, config keys and docs are in English.

- **Board** (`/`): categories, point values, and live scores. Questions that have been played go dark.
- **Question** (`/q/<cat>/<row>`): the question in big text. Tap **Show answer** to reveal the answer to the room.
  Each player has ✓ / ✗ buttons:
  - ✓ adds the question's value and sends you back to the board.
  - ✗ subtracts the value. That player is locked out of the question and the others can still try.
  - **Nobody** closes the question with no change to any score.
- **Regler** (`/regler`): a one-screen summary of the rules in Norwegian for the contestants, linked from the top bar.
- **Admin** (`⚙`, `/admin`): adjust scores by hand, undo, and reset the game.
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

Game progress is saved in `state.json` after every action. That means a reloaded phone browser or a
restarted server continues the same game. If you edit `quiz.json`, the next start begins a fresh game.

## The quiz file

Edit `quiz.json`, or point `KVISS_CONFIG` at another file. `quiz-example.json` is a full 5-player quiz
(Politikk, Sport, Musikk, Underholdning, Godt og Blandet). Try it with
`KVISS_CONFIG=quiz-example.json .venv/bin/python app.py`, or on the VPS add `KVISS_CONFIG=/opt/kviss/quiz-example.json`
to `/etc/kviss.env` and restart the service.

The format:

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
- **Add to Home Screen** and open Kviss from that icon. It then runs full screen in landscape, with no address bar on the TV.

## Ideas for after the MVP

- A separate `/host` page on a second device that shows answers privately, while the TV shows `/board`.
- Daily Doubles and a Final Jeopardy round with wagers.
- Upload or switch quiz files from the admin page instead of editing them on the server.
- Sound effects and a countdown timer.
