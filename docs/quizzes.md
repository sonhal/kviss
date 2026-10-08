# Managing quizzes

A quiz is a JSON document (format below). The first time the app starts with an empty database, it imports
`quiz.json` (or the file `KVISS_CONFIG` points at) and `quiz-example.json`, a full 5-player quiz (Politikk,
Sport, Musikk, Underholdning, Godt og Blandet). After that, the database is the only source. Editing
`quiz.json` does nothing until you upload it again.

Each quiz has a **slug**, a short name like `fredagskviss`. It is made from the title (`"Fredagskviss på Bærum"`
→ `fredagskviss-pa-baerum`), or you can set it yourself with a `"slug"` field. **Uploading a quiz with a slug that
already exists replaces it.** That is how you fix a typo. To keep both, give the new one another title or slug.

## Building a quiz in the browser

Open **Lag kviss** (`/lag`). **＋ Ny kviss** starts an empty quiz with one category of five questions (100–500);
the list under it opens a stored quiz for editing. The form covers everything in [the quiz format](#the-quiz-format):
title, players, categories and questions (add, delete and move them with ↑ ↓ ✕), music under **♪ Musikk** per
question, and the final question. A pasted YouTube link is turned into the video ID, and `start`/`end` can be typed
as seconds (`75`) or minutes (`1:15`).

- **Drafts are kept in the browser**, in `localStorage`, saved a moment after every keystroke. Closing the tab or
  reloading the phone keeps them, and the page lists them under **Utkast**. They are only in that browser: another
  device or a private window doesn't see them, and clearing site data deletes them. If the browser blocks storage
  (some private modes), the page says so, and the quiz is lost if the page is closed before it is saved.
- **Lagre kvissen** sends the quiz to the server (`POST /lag`), which checks it exactly like an upload. Problems
  are listed, and the field each one is about is outlined; tap a problem to jump to it. A saved quiz links straight
  to starting a game, and its draft is deleted, since the server has it now.
- **Editing a stored quiz** (`/lag/<slug>`) makes a draft from it that keeps its slug, so saving replaces it. Coming
  back later continues that draft, with a button to throw it away and start again from the stored version.
- **A new quiz never replaces another one by accident.** If its title gives a slug that is already taken, the page
  asks before replacing that quiz.
- **▶ Test** under **♪ Musikk** plays the clip as the question screen will: from Start, stopping by itself at Slutt
  (or at the end of the song), with the time shown while it plays. **■ Stopp** stops it, and only one clip plays at a
  time. A YouTube clip plays in the same invisible player as in the game, so a video whose owner blocks playing it
  outside YouTube says so here, before game night. An audio file must already be in the media folder on the server.
- **Last ned som fil** downloads the draft as quiz JSON, for a backup or for **Last opp kviss** on another server.
- The builder needs JavaScript; without it, the page points to **Last opp kviss**.

## Uploading from the browser

Open **Last opp kviss** (`/last-opp`), pick one or more `.json` files and tap **Last opp**. The checks, the
1 MB limit (for all the files together) and the replace-by-slug rule are the same as for the API below. Audio files
for music questions still have to be copied to the server first.

## The API

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

## The quiz format

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

## Music questions

A music question plays a song clip, and the players answer a question about it (title, artist, year …).
One clip is one question, and the host judges it like any other. The song comes either from **YouTube**
(only the sound is played, the video is never shown) or from an **audio file** such as an MP3.

### Add a YouTube song

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

### Add an MP3 (or other audio file)

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

### Fields

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

## Prompt for generating questions with an AI

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
