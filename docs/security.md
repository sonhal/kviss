# Security notes

- Gunicorn only listens on `127.0.0.1`. Caddy is the only thing exposed to the internet, and it terminates TLS.
  Without HTTPS, the Basic Auth password would travel in plain text.
- POSTs whose `Origin` header points at a different site are rejected, which blocks cross-site form
  attacks (CSRF) from other pages open in the same browser.
- The quiz API, the upload page and the quiz builder need the password like every other page. Request bodies over 1 MB are refused (`413`). Further
  protection of `/api/` (rate limits, IP allow-lists) belongs in the Caddy config in front of the app.
- All input is parsed by Pydantic models in `schemas.py` before the app uses it. An upload is checked in full
  before anything is written. Quiz JSON is checked strictly (no type coercion, no unknown fields, no
  `NaN`/`Infinity`, size limits). Every form post is checked too (player index, score change, verdict, redirect
  target); a value the pages never send gets a `400`.
- The database is only reached through parameterised SQL queries, so quiz text can't alter a query.
- All quiz text goes through Jinja's auto-escaping, so HTML in a question cannot inject scripts. The quiz builder
  only puts text into the page as text (`textContent` and input values, never `innerHTML`), and the stored quiz it
  edits is embedded with Jinja's `tojson`, which escapes `<`, `>` and `&` so a question can't end the script tag.
- Builder drafts are stored unencrypted in the browser's `localStorage`, outside the password. Anyone who can use
  that browser profile can read them, so on a shared computer, save or delete drafts when you're done.
- A `youtube` value must be exactly an 11-character ID (`A-Z a-z 0-9 _ -`), so the quiz file can't point the
  player at anything else. `/media/` serves only the audio files the current game's quiz names, from inside the `media/`
  folder, and is behind the same password as everything else. The builder's **▶ Test** uses `/lag/lyd/`, which serves
  any audio file (`.mp3`, `.m4a`, `.aac`, `.wav`) in `media/`, so a clip can be heard before the quiz is saved. It is
  behind the password too, refuses other file types, and can't reach outside the folder.
- The systemd unit runs as an unprivileged user with a read-only filesystem except for `/opt/kviss`.
- The app deliberately runs a **single** gunicorn worker. The current game is also held in memory in that one
  process, along with the question the TV shows for the host view, so do not raise `--workers`.
