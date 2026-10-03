"""Parsing and validation of everything users send to Kviss, with Pydantic.

Two kinds of input:

- Quiz JSON (API uploads and the `import` command). Validated in strict mode: a value
  must already have the right JSON type, so "100" or true is never silently turned into
  a number, and unknown keys are rejected so a typo like "anwser" is caught instead of
  ignored. Messages are in English, like the rest of the API.
- The HTML forms the host submits. Form values are always strings, so these models use
  Pydantic's normal (lax) mode to turn "2" into 2. Messages shown to the host are in
  Norwegian; anything that only a tampered request can send just gets a 400.
"""

import re
import unicodedata
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (BaseModel, BeforeValidator, ConfigDict, Field, PlainSerializer, StringConstraints,
                      ValidationError, ValidationInfo, field_validator, model_validator)
from pydantic_core import PydanticCustomError

MAX_PLAYERS = 30
MAX_GAME_NAME = 80
MAX_NAME = 40
MAX_CATEGORIES = 12
MAX_QUESTIONS = 20  # per category
MAX_TEXT = 1000     # a question or an answer
MAX_SCORE_CHANGE = 100_000  # one manual adjustment on the admin page
MAX_DAILY_DOUBLES = 10
MIN_WAGER = 5  # as on the TV show
YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{11}")
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
AUDIO_TYPES = {".mp3", ".m4a", ".aac", ".wav"}  # formats both Safari and Chrome play


class ConfigError(ValueError):
    """A quiz that can't be used. str() is every problem, one per line; .problems is the list."""

    def __init__(self, problems):
        self.problems = [problems] if isinstance(problems, str) else list(problems)
        super().__init__("\n".join(self.problems))


def slugify(text):
    """'Fredagskviss på Bærum' -> 'fredagskviss-pa-baerum'."""
    text = text.lower().translate(str.maketrans({"æ": "ae", "ø": "o", "å": "a"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:60].strip("-")


def _fail(message):
    # A custom error type keeps Pydantic from prefixing the message with "Value error, ".
    return PydanticCustomError("kviss", message)


# --- quiz JSON ----------------------------------------------------------------


def Text(max_length):
    """Non-empty text, with surrounding whitespace removed."""
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


# Seconds into a clip. Whole numbers stay whole in the saved quiz ("30", not "30.0").
Seconds = Annotated[float, Field(ge=0), PlainSerializer(lambda s: int(s) if s.is_integer() else s)]


class _Strict(BaseModel):
    # allow_inf_nan: Python's json module accepts NaN and Infinity, which are not JSON numbers.
    model_config = ConfigDict(strict=True, extra="forbid", allow_inf_nan=False)


class Question(_Strict):
    value: Annotated[int, Field(gt=0, le=1_000_000)]
    question: Text(MAX_TEXT)
    answer: Text(MAX_TEXT)
    # Music questions: a YouTube video ID or an audio file in the media folder, and the clip to play.
    youtube: str | None = None
    audio: Text(255) | None = None
    start: Seconds | None = None
    end: Seconds | None = None

    @field_validator("youtube")
    @classmethod
    def _youtube_id(cls, youtube):
        # Only an ID, never a URL, so the quiz can't point the player at anything else.
        if youtube is not None and not YOUTUBE_ID.fullmatch(youtube):
            raise _fail("must be an 11-character video ID like 'dQw4w9WgXcQ' "
                        f"(the part after v= in the link), got {youtube!r}")
        return youtube

    @field_validator("audio")
    @classmethod
    def _audio_file(cls, audio, info: ValidationInfo):
        """The file must exist inside the media folder, which comes from the validation context."""
        if audio is None:
            return None
        media_dir = (info.context or {}).get("media_dir")
        if media_dir is None:
            raise _fail("can't be checked without a media folder")
        media_dir = Path(media_dir)
        if any(ord(ch) < 32 for ch in audio):
            raise _fail("must be a file name")
        if Path(audio).suffix.lower() not in AUDIO_TYPES:
            raise _fail(f"must be one of {', '.join(sorted(AUDIO_TYPES))}, got {audio!r}")
        file = (media_dir / audio).resolve()
        if ".." in Path(audio).parts or not file.is_relative_to(media_dir.resolve()):
            raise _fail(f"must be inside {media_dir}")
        audio = Path(audio).as_posix()  # "./a.mp3" -> "a.mp3", the path browsers request
        if not file.is_file():
            raise _fail(f"audio file not found: {media_dir / audio}")
        return audio

    @model_validator(mode="after")
    def _clip(self):
        if self.youtube is None and self.audio is None:
            for field in ("start", "end"):
                if getattr(self, field) is not None:
                    raise _fail(f"'{field}' needs 'youtube' or 'audio'")
        elif self.youtube is not None and self.audio is not None:
            raise _fail("use either 'youtube' or 'audio', not both")
        if self.end is not None and self.end <= (self.start or 0):
            raise _fail("'end' must be a number of seconds after 'start'")
        return self


class Category(_Strict):
    name: Text(100)
    questions: Annotated[list[Question], Field(min_length=1, max_length=MAX_QUESTIONS)]


class Quiz(_Strict):
    title: Text(100)
    slug: Annotated[str, Field(max_length=60)] | None = None  # made from the title when left out
    # Names suggested on the new-game screen. The host can change them there.
    players: Annotated[list[Text(MAX_NAME)], Field(max_length=MAX_PLAYERS)] = []
    categories: Annotated[list[Category], Field(min_length=1, max_length=MAX_CATEGORIES)]

    @field_validator("slug")
    @classmethod
    def _slug(cls, slug):
        if slug is not None and not SLUG.fullmatch(slug):
            raise _fail(f"must be lowercase letters, digits and dashes, like 'fredagskviss-2', got {slug!r}")
        return slug

    @field_validator("players")
    @classmethod
    def _unique_players(cls, players):
        if len({p.casefold() for p in players}) < len(players):
            raise _fail("player names must be unique")
        return players

    @model_validator(mode="after")
    def _default_slug(self):
        if self.slug is None:
            self.slug = slugify(self.title)
            if not self.slug:
                raise _fail("'title' needs at least one letter or digit, or give a 'slug'")
        return self


# Messages for the Pydantic errors that quiz writers run into most, phrased to follow the field name.
_FRIENDLY = {
    "missing": "is required",
    "extra_forbidden": "is not a known field (check the spelling)",
    "model_type": "must be an object",
    "model_attributes_type": "must be an object",
    "list_type": "must be a list",
    "string_type": "must be text",
    "int_type": "must be a whole number",
    "float_type": "must be a number",
    "finite_number": "must be a number",
}
_LIST_LABELS = {"categories": "category", "questions": "question", "players": "player"}


def _where(loc, data):
    """('categories', 0, 'questions', 1, 'value') -> "category 'Sport', question #2, 'value'".

    Looks up category names in the uploaded data, so the writer can find the question.
    """
    parts, node, i = [], data, 0
    while i < len(loc):
        key = loc[i]
        child = node.get(key) if isinstance(node, dict) and isinstance(key, str) else None
        if key in _LIST_LABELS and i + 1 < len(loc) and isinstance(loc[i + 1], int):
            index = loc[i + 1]
            item = child[index] if isinstance(child, list) and index < len(child) else None
            name = item.get("name") if key == "categories" and isinstance(item, dict) else None
            parts.append(f"{_LIST_LABELS[key]} {name.strip()!r}" if isinstance(name, str) and name.strip()
                         else f"{_LIST_LABELS[key]} #{index + 1}")
            node, i = item, i + 2
        else:
            parts.append(f"'{key}'")
            node, i = child, i + 1
    return ", ".join(parts)


def quiz_problems(error: ValidationError, data):
    """Readable, one-line messages for each problem in a quiz."""
    problems = []
    for err in error.errors(include_url=False):
        where = _where(err["loc"], data)
        friendly = _FRIENDLY.get(err["type"])
        if friendly is None and err["type"] == "string_too_short" and err.get("ctx", {}).get("min_length") == 1:
            friendly = "must not be empty"
        if friendly:
            problems.append(f"{where} {friendly}" if where else f"top level {friendly}")
        else:
            problems.append(f"{where}: {err['msg']}" if where else err["msg"])
    return problems


def parse_quiz(data, media_dir):
    """Validate parsed JSON as a quiz and return it as a plain dict, ready to be saved.

    Optional fields that were left out stay out, so a saved quiz looks like the uploaded one.
    Raises ConfigError listing every problem.
    """
    try:
        quiz = Quiz.model_validate(data, context={"media_dir": Path(media_dir)})
    except ValidationError as e:
        raise ConfigError(quiz_problems(e, data)) from None
    return quiz.model_dump(exclude_none=True)


# --- forms --------------------------------------------------------------------


class FormError(ValueError):
    """A form the host can correct; the message is shown on the page (in Norwegian)."""


def _lines(text):
    """The players textarea: one name per line, blank lines ignored."""
    if isinstance(text, str):
        return [line.strip() for line in text.splitlines() if line.strip()]
    return text


class _Form(BaseModel):
    model_config = ConfigDict(extra="ignore")  # buttons and other fields the server doesn't use


Checkbox = Annotated[bool, BeforeValidator(lambda v: v == "yes")]  # value="yes" when ticked, absent if not


class NewGameForm(_Form):
    name: Annotated[str, StringConstraints(strip_whitespace=True)] = ""  # empty = named after the quiz
    players: Annotated[list[str], BeforeValidator(_lines)] = []
    confirm: Checkbox = False
    # Daily Doubles: hidden on this many tiles, picked at random when the game starts.
    daily_double: Checkbox = False
    daily_doubles: int = 0  # 0 when daily_double is off

    @field_validator("daily_doubles", mode="before")
    @classmethod
    def _daily_doubles(cls, value, info: ValidationInfo):
        if not info.data.get("daily_double"):
            return 0  # switched off: the number field is ignored
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise _fail("Skriv antall Dagens dobbel som et helt tall.") from None
        if not 1 <= value <= MAX_DAILY_DOUBLES:
            raise _fail(f"Antall Dagens dobbel må være fra 1 til {MAX_DAILY_DOUBLES}.")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, name):
        if len(name) > MAX_GAME_NAME:
            raise _fail(f"Navnet på spillet kan være maks {MAX_GAME_NAME} tegn.")
        return name

    @field_validator("players")
    @classmethod
    def _players(cls, names):
        if not names:
            raise _fail("Skriv inn minst én deltaker.")
        if len(names) > MAX_PLAYERS:
            raise _fail(f"Maks {MAX_PLAYERS} deltakere.")
        if any(len(n) > MAX_NAME for n in names):
            raise _fail(f"Navn kan være maks {MAX_NAME} tegn.")
        if len({n.casefold() for n in names}) < len(names):
            raise _fail("To deltakere har samme navn.")
        return names


PlayerIndex = Annotated[int, Field(ge=0, lt=MAX_PLAYERS)]


class JudgeForm(_Form):
    result: Literal["correct", "wrong", "nobody"]
    player: PlayerIndex | None = None  # not sent with "nobody"
    reveal: bool = False  # "1" if the answer was showing when the host judged, else "0"

    @model_validator(mode="after")
    def _player_for_verdict(self):
        if self.result != "nobody" and self.player is None:
            raise _fail("'player' is required")
        return self


class WagerForm(_Form):
    """The bet on a Daily Double. The upper limit depends on the game, so Game.wager checks it."""
    player: PlayerIndex | None = None
    amount: int

    @field_validator("player", mode="before")
    @classmethod
    def _picked(cls, player):
        if player in (None, ""):
            raise _fail("Velg hvem som fant Dagens dobbel.")
        return player

    @field_validator("amount", mode="before")
    @classmethod
    def _whole_number(cls, amount):
        try:
            return int(amount)
        except (TypeError, ValueError):
            raise _fail("Skriv innsatsen som et helt tall.") from None


class AdjustForm(_Form):
    player: PlayerIndex
    delta: Annotated[int, Field(ge=-MAX_SCORE_CHANGE, le=MAX_SCORE_CHANGE)]


class ConfirmForm(_Form):
    confirm: Literal["yes"]


class UndoForm(_Form):
    next: Literal["admin", "board"] = "board"


def parse_form(model, form):
    """Validate a submitted form (a dict of strings). Raises FormError with the first message.

    Callers turn that into a 400: either the page with the message, or a bare 400 for
    values that only a hand-made request could send.
    """
    try:
        return model.model_validate(form)
    except ValidationError as e:
        raise FormError(e.errors(include_url=False)[0]["msg"]) from None
