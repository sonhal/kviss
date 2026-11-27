"""Uploaded audio files for music questions: what format a file really is, and saving it in the media folder.

Files are kept as uploaded, not converted: MP3, AAC (in an MP4/M4A container or as raw ADTS) and WAV play in
both Safari and Chrome. The format is decided from the file's contents, never its name or the browser's
Content-Type, and the file is stored with the extension that matches what it is. A file that is not one of
these formats is refused, so the media folder only ever holds audio the game can play.
"""

import hashlib
import os
import struct
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from schemas import AUDIO_TYPES, slugify

MAX_AUDIO_MB = 25  # per file; a 4-minute MP3/M4A song is 4-10 MB
MAX_AUDIO_UPLOAD = MAX_AUDIO_MB * 1024 * 1024  # bytes
SNIFF_BYTES = 64 * 1024  # how far into an MP3/AAC file (after an ID3 tag) the first frame may start


class AudioError(ValueError):
    """The file can't be used. str() is a message for the person who uploaded it (Norwegian, as the UI)."""


class AudioTooLarge(AudioError):
    def __init__(self, max_size=MAX_AUDIO_UPLOAD):
        super().__init__(f"Filen er for stor (maks {max_size // (1024 * 1024)} MB).")


@dataclass(frozen=True)
class AudioFormat:
    ext: str    # the extension the file is stored with, one of schemas.AUDIO_TYPES
    label: str  # shown in the builder, e.g. "MP3"


MP3 = AudioFormat(".mp3", "MP3")
M4A = AudioFormat(".m4a", "MPEG-4-lyd (M4A)")
AAC = AudioFormat(".aac", "AAC")
WAV = AudioFormat(".wav", "WAV")


# --- MP3 and raw AAC (ADTS): a stream of frames, each with a sync word ---------------------------

# Layer III bitrates in kbit/s by bitrate index: MPEG-1, and MPEG-2/2.5. Index 0 ("free") and 15 are not used.
_MP3_BITRATES = {
    1: (None, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, None),
    2: (None, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, None),
}
_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}  # by version bits


def _mp3_frame_length(data, i):
    """The length of the MPEG Layer III frame starting at data[i], or None if there is no valid header there."""
    if i + 4 > len(data) or data[i] != 0xFF or data[i + 1] & 0xE0 != 0xE0:
        return None
    version = (data[i + 1] >> 3) & 3  # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5, 1 = reserved
    layer = (data[i + 1] >> 1) & 3    # 1 = Layer III
    if version == 1 or layer != 1:
        return None
    bitrate = _MP3_BITRATES[1 if version == 3 else 2][data[i + 2] >> 4]
    rate_index = (data[i + 2] >> 2) & 3
    if bitrate is None or rate_index == 3:
        return None
    rate = _MP3_RATES[version][rate_index]
    padding = (data[i + 2] >> 1) & 1
    return (144 if version == 3 else 72) * bitrate * 1000 // rate + padding


def _adts_frame_length(data, i):
    """The length of the ADTS (raw AAC) frame starting at data[i], or None if there is no valid header there."""
    if i + 7 > len(data) or data[i] != 0xFF or data[i + 1] & 0xF6 != 0xF0:  # 12-bit sync, layer 00
        return None
    if (data[i + 2] >> 2) & 0xF > 12:  # sampling frequency index
        return None
    length = ((data[i + 3] & 3) << 11) | (data[i + 4] << 3) | (data[i + 5] >> 5)
    return length if length >= 7 else None


def _frames(data, start, frame_length, count=3):
    """True if `count` frames follow each other from `start` (or the data ends after at least two).
    One sync word can happen by chance in any file; a chain of frames whose lengths line up does not."""
    i = start
    for n in range(count):
        length = frame_length(data, i)
        if length is None:
            return n >= 2 and i >= len(data)
        i += length
    return True


def _id3_end(f):
    """Where the audio starts after any ID3v2 tags (MP3 files usually start with one; some AAC files too)."""
    pos = 0
    while True:
        f.seek(pos)
        header = f.read(10)
        if len(header) < 10 or header[:3] != b"ID3" or any(b & 0x80 for b in header[6:10]):
            return pos
        size = 0
        for b in header[6:10]:  # "syncsafe": 7 bits per byte
            size = (size << 7) | b
        pos += 10 + size + (10 if header[5] & 0x10 else 0)  # flag 0x10: a footer follows


def _sniff_stream(f):
    f.seek(_id3_end(f))
    data = f.read(SNIFF_BYTES)
    for i in range(len(data)):
        if data[i] != 0xFF:
            continue
        if _frames(data, i, _mp3_frame_length):
            return MP3
        if _frames(data, i, _adts_frame_length):
            return AAC
    return None


# --- MPEG-4 (.m4a, .mp4): a tree of boxes ------------------------------------------------------

_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
# Sample entries for audio codecs. Only AAC ("mp4a", which also covers MP3 in MP4) plays in both Safari and Chrome.
_CODEC_NAMES = {b"alac": "Apple Lossless (ALAC)", b"ac-3": "Dolby Digital (AC-3)", b"ec-3": "Dolby Digital Plus",
                b"Opus": "Opus", b"fLaC": "FLAC", b"drms": "kopibeskyttet (DRM)", b"enca": "kopibeskyttet (DRM)"}


def _boxes(f, start, end):
    """(type, payload start, payload end) for each box between start and end in an ISO base media file."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        size, kind = struct.unpack(">I4s", f.read(8))
        header = 8
        if size == 1:  # a 64-bit size follows
            raw = f.read(8)
            if len(raw) < 8:
                return
            size, header = struct.unpack(">Q", raw)[0], 16
        elif size == 0:  # to the end of the file
            size = end - pos
        if size < header or pos + size > end:
            return
        yield kind, pos + header, pos + size
        pos += size


def _mp4_tracks(f, start, end, depth=0):
    """(handler type, first sample entry type) for every track in the moov box."""
    tracks = []
    for kind, body, box_end in _boxes(f, start, end):
        if kind == b"trak":
            handler = codec = None
            for path_kind, path_body, path_end in _walk(f, body, box_end):
                if path_kind == b"hdlr" and path_end - path_body >= 12:
                    f.seek(path_body + 8)  # version/flags, pre_defined
                    handler = f.read(4)
                elif path_kind == b"stsd" and path_end - path_body >= 16 and codec is None:
                    f.seek(path_body + 12)  # version/flags, entry count, first entry's size
                    codec = f.read(4)
            tracks.append((handler, codec))
        elif kind in _CONTAINERS and depth < 2:
            tracks += _mp4_tracks(f, body, box_end, depth + 1)
    return tracks


def _walk(f, start, end, depth=0):
    """Every box inside a track, depth first (the tree is shallow: trak/mdia/minf/stbl/stsd)."""
    for kind, body, box_end in _boxes(f, start, end):
        yield kind, body, box_end
        if kind in _CONTAINERS and depth < 6:
            yield from _walk(f, body, box_end, depth + 1)


def _sniff_mp4(f, size):
    f.seek(0)
    head = f.read(12)
    if len(head) < 12 or head[4:8] != b"ftyp":
        return None
    sound = []
    for kind, body, box_end in _boxes(f, 0, size):  # moov is often at the end, after the audio data
        if kind == b"moov":
            sound += [codec for handler, codec in _mp4_tracks(f, body, box_end) if handler == b"soun"]
    if not sound:
        raise AudioError("Filen er en MPEG-4-fil, men har ikke noe lydspor.")
    if b"mp4a" in sound:
        return M4A
    name = _CODEC_NAMES.get(sound[0], repr(sound[0].decode("latin-1")) if sound[0] else "ukjent")
    raise AudioError(f"Lydsporet i filen er {name}, som ikke spilles i alle nettlesere. "
                     "Lagre den som AAC (.m4a) eller MP3 og prøv igjen.")


# --- all formats -------------------------------------------------------------------------------


def sniff(path):
    """The AudioFormat of the file at path. Raises AudioError, with a message for the user, if it isn't one
    the game can play."""
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as f:
        head = f.read(12)
        if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
            return WAV
        fmt = _sniff_mp4(f, size) or _sniff_stream(f)
    if fmt is None:
        raise AudioError("Filen er ikke en lydfil kviss kan spille. Bruk MP3, M4A/MP4 (AAC), AAC eller WAV.")
    return fmt


# --- saving --------------------------------------------------------------------------------------

_save_lock = threading.Lock()  # choosing a name and claiming it is one step


def _digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def clean_name(filename):
    """'Take On Me (Remastered).MP3' -> 'take-on-me-remastered', the name an upload is stored under."""
    stem = Path((filename or "").replace("\\", "/")).stem  # browsers on Windows may send a full path
    return slugify(stem) or "lyd"


def audio_files(media_dir):
    """The audio files in media_dir, sub-folders included, as the names a quiz's "audio" field uses
    ("take-on-me.mp3", "80s/take-on-me.mp3"), sorted. Hidden files and folders (an upload's temporary file)
    and other file types are left out."""
    media_dir = Path(media_dir)
    if not media_dir.is_dir():
        return []
    names = []
    for path in media_dir.rglob("*"):
        rel = path.relative_to(media_dir)
        if (path.suffix.lower() in AUDIO_TYPES and not any(part.startswith(".") for part in rel.parts)
                and path.is_file()):
            names.append(rel.as_posix())
    return sorted(names, key=lambda name: (name.lower(), name))


def used_bytes(media_dir):
    """The total size of the audio files in media_dir."""
    media_dir = Path(media_dir)
    return sum((media_dir / name).stat().st_size for name in audio_files(media_dir))


def size_text(size):
    """A size as the pages show it, with a Norwegian decimal comma: 9216 -> '9 kB', 1572864 -> '1,5 MB',
    64424509440 -> '60,0 GB'."""
    if size < 1024 * 1024:
        return f"{round(size / 1024)} kB"
    mb = size / (1024 * 1024)
    return (f"{mb / 1024:.1f} GB" if mb >= 1024 else f"{mb:.1f} MB").replace(".", ",")


class MediaFull(AudioError):
    def __init__(self, used, max_total):
        super().__init__(f"Lydmappen er full: {size_text(used)} av {size_text(max_total)} er brukt. "
                         "Slett lydfiler som ikke brukes på siden Lydfiler, og prøv igjen.")


def _claim(target):
    """Create target as an empty file, so no one else can take the name. False if it exists already."""
    try:
        os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    except FileExistsError:
        return False
    return True


def save_upload(stream, filename, media_dir, max_size=MAX_AUDIO_UPLOAD, max_total=None):
    """Store an uploaded audio file in media_dir. Returns (name, AudioFormat, reused).

    name is the file name relative to media_dir, as the quiz's "audio" field names it: the uploaded name made
    safe ("take-on-me.mp3"), with -2, -3 ... added if a different file already has that name. If the same
    file (same contents) is already there under that name, it is reused and reused is True.
    max_total: the most the media folder's audio files may add up to, in bytes (None: no limit). A reused
    file doesn't count, since it takes no more space.
    Raises AudioError if the file is too big, not a format the game can play, or doesn't fit (MediaFull).
    """
    media_dir = Path(media_dir)
    media_dir.mkdir(parents=True, exist_ok=True)
    # The temporary file is in media_dir, so the final step is a rename on the same filesystem. Its name starts
    # with a dot and has no audio extension, so nothing serves it.
    fd, tmp = tempfile.mkstemp(dir=media_dir, prefix=".upload-")
    tmp = Path(tmp)
    try:
        size = 0
        digest = hashlib.sha256()
        with os.fdopen(fd, "wb") as out:
            while chunk := stream.read(1 << 20):
                size += len(chunk)
                if size > max_size:
                    raise AudioTooLarge(max_size)
                digest.update(chunk)
                out.write(chunk)
        if size == 0:
            raise AudioError("Filen er tom.")
        fmt = sniff(tmp)
        os.chmod(tmp, 0o644)  # mkstemp makes it 0600; the default for files copied in by hand
        base, digest = clean_name(filename), digest.hexdigest()
        with _save_lock:
            for n in range(1, 1000):
                name = f"{base}{fmt.ext}" if n == 1 else f"{base}-{n}{fmt.ext}"
                target = media_dir / name
                if target.exists():
                    if target.is_file() and target.stat().st_size == size and _digest(target) == digest:
                        return name, fmt, True
                    continue
                if max_total is not None and (used := used_bytes(media_dir)) + size > max_total:
                    raise MediaFull(used, max_total)
                if not _claim(target):  # another process took the name in the meantime
                    continue
                os.replace(tmp, target)
                return name, fmt, False
        raise AudioError("Fant ikke et ledig filnavn. Gi filen et annet navn.")
    finally:
        tmp.unlink(missing_ok=True)


def rename(media_dir, name, new_name):
    """Rename the audio file `name` (as audio_files lists it) to new_name, made safe like an upload's name
    ("A-ha – Take On Me" -> "a-ha-take-on-me"). The folder and extension stay. Returns the new name.
    Raises AudioError if new_name is empty after cleaning or another file has it."""
    media_dir = Path(media_dir)
    stem = slugify(new_name or "")
    if not stem:
        raise AudioError("Skriv et nytt navn med minst én bokstav eller ett tall.")
    old = Path(name)
    new = (old.parent / f"{stem}{old.suffix.lower()}").as_posix()
    if new == name:
        return name
    with _save_lock:
        if not _claim(media_dir / new):
            raise AudioError(f"Det finnes allerede en fil som heter «{new}».")
        try:
            os.replace(media_dir / name, media_dir / new)
        except OSError:
            (media_dir / new).unlink(missing_ok=True)
            raise
    return new
