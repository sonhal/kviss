# Deploying kviss

Two supported ways to run kviss in production: [Docker](#run-with-docker) or [systemd + venv + Caddy on a VPS](#deploy-to-a-vps-systemd--venv--caddy). Either way, put HTTPS in front and set `KVISS_PASSWORD`; see [security.md](security.md).

## Run with Docker

The image holds the app only. Everything that changes lives in **`/data`** inside the container, so mount a
volume there:

- `/data/kviss.db` is the database, with `kviss.db-wal` / `kviss.db-shm` next to it while the app runs. Mount the
  **folder**, not just the `.db` file, because SQLite creates those extra files beside the database.
- `/data/media/` holds audio files for music questions.

Use the same environment variables as for systemd (`KVISS_PASSWORD`, `KVISS_TZ`, …). `KVISS_DB` and `KVISS_MEDIA`
are already set to the paths above. On first start, an empty database imports `quiz.json` and `quiz-example.json`
from the image, as described in [quizzes.md](quizzes.md).

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
- **Version.** The start page's footer shows the version. Release images from `ghcr.io/sonhal/kviss` carry their
  tag (`v0.5.0`); an image built locally with `docker compose up -d --build` says `dev`, since `.git` isn't copied
  into the image. Pass the tag yourself with `docker compose build --build-arg VERSION=$(git describe --tags)`.
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
(`docker ps` shows `healthy`). The same single-gunicorn-worker rule from [security.md](security.md) applies: run **one**
container per database, and don't scale the service.

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

**Adding or changing quizzes:** upload them through the API ([quizzes.md](quizzes.md)). No restart
is needed. Audio files for music questions are not in git: copy them to `/opt/kviss/media/` as described in
[Add an MP3](quizzes.md#add-an-mp3-or-other-audio-file).

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
folder), `KVISS_CONFIG` (quiz imported on first start), `KVISS_TZ` (time zone for dates, default `Europe/Oslo`)
and `KVISS_VERSION` (the version in the start page's footer; by default `git describe` of the checkout, such as
`v0.5.0` or `v0.5.0-2-gabc1234` for two commits past it).

