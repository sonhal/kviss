# Kviss as a container. All state lives in /data: the SQLite database (kviss.db plus its
# -wal/-shm files) and the media/ folder with audio for music questions. Mount a volume there.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KVISS_DB=/data/kviss.db \
    KVISS_MEDIA=/data/media

# tzdata (pip): a fallback time zone database, so KVISS_TZ keeps working even if the base image
# drops Debian's /usr/share/zoneinfo. Python checks the system files first.
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt tzdata

# A fixed, unprivileged UID/GID so a bind-mounted host folder can be chown'ed to match (1000:1000).
RUN groupadd --system --gid 1000 kviss \
    && useradd --system --uid 1000 --gid kviss --home-dir /app --shell /usr/sbin/nologin kviss \
    && mkdir -p /data/media \
    && chown -R kviss:kviss /data

WORKDIR /app
COPY app.py schemas.py quiz.json quiz-example.json /app/
COPY static /app/static
COPY templates /app/templates

# The version shown on the start page. CI's Release job passes the tag (v0.5.0); a local build says "dev".
ARG VERSION=dev
ENV KVISS_VERSION=$VERSION

USER kviss
# A directory, not a single file: SQLite writes kviss.db-wal and kviss.db-shm next to the database.
VOLUME ["/data"]
EXPOSE 8000

# Any HTTP answer, including 401 when a password is set, means the app is up.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request as u, urllib.error as e, sys\ntry: u.urlopen('http://127.0.0.1:8000/', timeout=4)\nexcept e.HTTPError: pass\nexcept Exception: sys.exit(1)"]

# One worker on purpose: game state lives in that process. Threads handle concurrency.
# No access log, as with systemd: the host view polls every 1.5 s and would fill the container log.
# --worker-tmp-dir: gunicorn's heartbeat file goes to memory, so a read-only root filesystem works.
CMD ["gunicorn", "--workers", "1", "--threads", "4", "--bind", "0.0.0.0:8000", "--worker-tmp-dir", "/dev/shm", "app:create_app()"]
