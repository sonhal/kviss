# Playing a game

How each screen behaves, in detail. The screens are in Norwegian; their names are given as they appear.

## Screens and rules

- **Landing page** (`/`, where the home-screen icon opens): the game on the TV right now, with its standings and a
  **Fortsett** button back to the board, a **Nytt spill** button, and **Tidligere spill**, the last 50 finished
  games with their date, winner and how far they got. Pages that need a game send you here when none is running.
- **Nytt spill** (`/nytt`): pick a stored quiz, then name the game and type the teams or players, one per line.
  The name is pre-filled as "quiz title · date" and shows on the TV, the host view, the landing page and the
  results. Leave it empty to name the game after the quiz. The list shows how
  many times each quiz has been played and when. Starting a game ends the one on the TV. If that game was
  half-way through you have to tick a box first. A game where nothing was scored is just dropped. The others are
  kept in the history. When the podium is showing, the top bar gets a **Nytt spill** button.
- **Lag kviss** (`/lag`, linked from the landing page, **Nytt spill**, upload and admin): write a quiz in the
  browser, or open a stored one to change it. See [Building a quiz in the browser](quizzes.md#building-a-quiz-in-the-browser).
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
- **Finale** (Final Jeopardy, for quizzes with a [`final`](quizzes.md#the-quiz-format) question): tick **Med finale** on
  **Nytt spill** and set the countdown (30 seconds by default; the next game suggests the time you used last).
  For a quiz without a final question the box is greyed out, and the quiz list marks the quizzes that have one. When the board is empty, the TV shows the final's category instead of the podium, and which teams
  play: everyone above 0 points. Teams write their bet (0 up to their whole score) and their answer on paper.
  **Vis spørsmålet** shows the question with the countdown, and teams write their answer and turn the paper face
  down. **Skriv inn innsatsene** then lets the host type in every team's bet (checked against its score), and only
  then does **Vis svaret** show the correct answer, so nobody can change their paper after seeing it. Each team
  turns its paper and the host taps ✓ (adds the bet) or ✗ (subtracts it), in any order; the teams are listed
  lowest score first, so the leader comes last. Every result stays on the TV until **Se sluttresultat** goes on to
  the podium. If nobody is above 0, the final is skipped. The countdown carries on if the page is reloaded, the host
  view shows the answer and the bets, **Angre** steps back one step at a time, and the results page lists each
  team's bet.
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
  See [Music questions](quizzes.md#music-questions).
- **Admin** (`⚙`, `/admin`): start a new game, adjust scores by hand, undo, and reset the game. It also lists
  every music question, so you can test that each clip plays before the game. **Historikk** lists the last 20
  games with their quiz, date and final standings, each linking to its results page.
- **Undo** reverts the last scoring action (up to 50 steps), for when you mis-tap.
- When every question has been played, the board switches to a **Kahoot-style podium**. **You control the
  reveal:** tap the screen (or the pulsing **Avslør …** button) to raise 3rd place, tap again for 2nd, and once
  more for the winner and confetti. Places 4+ appear under the podium at the end. Tied teams share a place.
  Reload the page to replay the reveal, and use **Angre** if the last question was judged wrong.
  (Without JavaScript, the podium plays the same reveal automatically.)

## Polish (JavaScript and modern CSS, all optional)

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

## Game-night tips

- Hold the phone in **landscape**. The layout is sized for a 16:9 TV.
- Turn off auto-lock and auto-rotate. Screen-mirroring (AirPlay/Chromecast) mirrors your whole phone,
  so silence notifications (Do Not Disturb).
- **Sound on the TV:** AirPlay mirroring from an **iPhone** sends the sound to the TV automatically. From a
  **Mac** (AirPlay or HDMI), check that the TV is selected as the sound output (Control Center → Sound) and
  that its volume is up. Play a clip from the admin list before the guests arrive.
- **Add to Home Screen** and open Kviss from that icon. It then runs full screen in landscape, with no address bar on the TV.
