"""Uploading audio files for music questions: audio.py's format check and the builder's POST /lag/lyd.

The test files are built byte by byte, as small as each format allows, so the tests need no encoder.
"""

import base64
import io
import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.datastructures import FileStorage
from werkzeug.test import encode_multipart

from app import create_app
from audio import AAC, M4A, MP3, WAV, AudioError, clean_name, save_upload, sniff

# MPEG-1 Layer III, 128 kbit/s, 44.1 kHz, no padding: 144 * 128000 / 44100 = 417 bytes per frame.
MP3_FRAME = b"\xff\xfb\x90\x00" + b"\0" * 413


def mp3(frames=4, tag=b"", body=b""):
    """An MP3, optionally with an ID3v2 tag of len(tag) bytes in front."""
    head = b""
    if tag:
        size = len(tag)
        head = b"ID3\x03\x00\x00" + bytes((size >> 21 & 0x7F, size >> 14 & 0x7F, size >> 7 & 0x7F, size & 0x7F)) + tag
    return head + MP3_FRAME * frames + body


def adts(frames=4):
    """Raw AAC: ADTS frames of 64 bytes (MPEG-4, AAC LC, 44.1 kHz, stereo)."""
    length = 64
    header = bytes((0xFF, 0xF1, 0x50, 0x80 | (length >> 11), (length >> 3) & 0xFF, ((length & 7) << 5) | 0x1F, 0xFC))
    return (header + b"\0" * (length - 7)) * frames


def wav():
    fmt = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    data = b"\0" * 160
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data)


def box(kind, payload=b""):
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def mp4(*tracks, moov_last=True):
    """An MP4/M4A file with one track per (handler, sample entry) pair, e.g. (b"soun", b"mp4a").
    By default the moov box comes after the audio data, as most encoders write it."""
    traks = b""
    for handler, codec in tracks:
        hdlr = box(b"hdlr", b"\0" * 8 + handler + b"\0" * 12 + b"\0")
        stsd = box(b"stsd", b"\0" * 4 + struct.pack(">I", 1) + box(codec, b"\0" * 28))
        traks += box(b"trak", box(b"mdia", hdlr + box(b"minf", box(b"stbl", stsd))))
    parts = [box(b"ftyp", b"M4A \0\0\0\0M4A isom"), box(b"mdat", b"\0" * 100)]
    moov = box(b"moov", box(b"mvhd", b"\0" * 100) + traks)
    return b"".join(parts + [moov] if moov_last else parts[:1] + [moov] + parts[1:])


class SniffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def sniff(self, data):
        path = self.tmp / "file"
        path.write_bytes(data)
        return sniff(path)

    def refused(self, data):
        with self.assertRaises(AudioError) as caught:
            self.sniff(data)
        return str(caught.exception)

    def test_formats_that_play_in_safari_and_chrome(self):
        self.assertEqual(self.sniff(mp3()), MP3)
        self.assertEqual(self.sniff(mp3(tag=b"\0" * 200_000)), MP3)  # cover art: the tag is skipped, not searched
        self.assertEqual(self.sniff(b"\0" * 100 + mp3()), MP3)  # padding before the first frame
        self.assertEqual(self.sniff(adts()), AAC)
        self.assertEqual(self.sniff(wav()), WAV)
        self.assertEqual(self.sniff(mp4((b"soun", b"mp4a"))), M4A)
        self.assertEqual(self.sniff(mp4((b"soun", b"mp4a"), moov_last=False)), M4A)
        self.assertEqual(self.sniff(mp4((b"vide", b"avc1"), (b"soun", b"mp4a"))), M4A)  # a video's sound plays

    def test_the_name_and_type_dont_matter_only_the_contents(self):
        self.assertEqual(self.sniff(mp3()).ext, ".mp3")
        self.assertEqual(self.sniff(mp4((b"soun", b"mp4a"))).ext, ".m4a")

    def test_other_files_are_refused(self):
        not_audio = "ikke en lydfil kviss kan spille"
        self.assertIn(not_audio, self.refused(b"hello"))
        self.assertIn(not_audio, self.refused(b"<html>" + b"\xff\xfb\x90\x00" + b"x" * 100))  # one stray sync word
        self.assertIn(not_audio, self.refused(b"fLaC" + b"\0" * 1000))
        self.assertIn(not_audio, self.refused(b"OggS" + b"\0" * 1000))
        self.assertIn(not_audio, self.refused(b"ID3\x03\x00\x00\x00\x00\x00\x10" + b"\0" * 16))  # a tag, no audio
        self.assertIn("ikke noe lydspor", self.refused(mp4((b"vide", b"avc1"))))
        self.assertIn("Apple Lossless", self.refused(mp4((b"soun", b"alac"))))
        self.assertIn("kopibeskyttet", self.refused(mp4((b"soun", b"drms"))))

    def test_broken_mp4_boxes_are_refused_not_crashed_on(self):
        good = mp4((b"soun", b"mp4a"))
        for data in (good[:40], good[:-10], box(b"ftyp", b"M4A ") + struct.pack(">I", 1) + b"moov" + b"\xff" * 8,
                     box(b"ftyp", b"M4A ") + struct.pack(">I", 3) + b"moov"):
            with self.assertRaises(AudioError):
                self.sniff(data)

    def test_clean_names(self):
        self.assertEqual(clean_name("Take On Me (Remastered).MP3"), "take-on-me-remastered")
        self.assertEqual(clean_name("Blåbærsyltetøy.m4a"), "blabaersyltetoy")
        self.assertEqual(clean_name("C:\\Users\\x\\Musikk\\a-ha.mp3"), "a-ha")
        self.assertEqual(clean_name("../../etc/passwd"), "passwd")
        self.assertEqual(clean_name("♪♪.mp3"), "lyd")
        self.assertEqual(clean_name(""), "lyd")

    def test_save_leaves_nothing_behind_when_refused(self):
        media = self.tmp / "media"
        for data in (b"not audio", mp3(frames=40)):
            with self.assertRaises(AudioError):
                save_upload(io.BytesIO(data), "x.mp3", media, max_size=10_000)
        self.assertEqual(list(media.iterdir()), [])


class AppTestCase(unittest.TestCase):
    """An app with an empty database and media folder, and helpers to upload to it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.media = self.tmp / "media"  # doesn't exist yet: the first upload makes it
        self.client = self.make_client()

    def make_client(self, password=""):
        return create_app(self.tmp / "kviss.db", password=password, media_dir=self.media, seed=[]).test_client()

    def post(self, data, filename, client=None, url="/lag/lyd", field="file", **kwargs):
        # Encoded here, in memory: the test client spools bodies over 500 kB to a file it never closes.
        boundary, body = encode_multipart({field: FileStorage(io.BytesIO(data), filename)})
        return (client or self.client).post(url, data=body, content_type=f"multipart/form-data; boundary={boundary}",
                                            **kwargs)

    def files(self):
        return sorted(p.name for p in self.media.iterdir())


class UploadRouteTest(AppTestCase):

    def test_upload_stores_the_file_under_a_clean_name(self):
        resp = self.post(mp3(), "Take On Me.mp3")
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.get_json(), {"file": "take-on-me.mp3", "format": "MP3", "reused": False})
        self.assertEqual((self.media / "take-on-me.mp3").read_bytes(), mp3())
        self.assertEqual(self.files(), ["take-on-me.mp3"])  # no temporary files left
        self.assertEqual(self.post(adts(), "Sang.aac").get_json()["file"], "sang.aac")
        self.assertEqual(self.post(wav(), "Sang.wav").get_json()["file"], "sang.wav")
        # The builder can play it right away, and a quiz can use it.
        resp = self.client.get("/lag/lyd/take-on-me.mp3")
        self.assertEqual((resp.status_code, resp.mimetype), (200, "audio/mpeg"))
        resp.close()
        quiz = {"title": "Musikk", "categories": [{"name": "Låter", "questions": [
            {"value": 100, "question": "Hvem?", "answer": "a-ha", "audio": "take-on-me.mp3"}]}]}
        self.assertEqual(self.client.post("/lag", json={"quiz": quiz}).status_code, 201)

    def test_mpeg4_audio_is_stored_as_m4a(self):
        for name in ("Låt.m4a", "Låt.mp4"):
            resp = self.post(mp4((b"soun", b"mp4a")), name)
            self.assertEqual(resp.get_json()["file"], "lat.m4a")
        resp = self.client.get("/lag/lyd/lat.m4a")
        self.assertEqual(resp.mimetype, "audio/mp4")
        resp.close()

    def test_the_extension_follows_the_contents(self):
        self.assertEqual(self.post(mp3(), "sang.m4a").get_json()["file"], "sang.mp3")
        self.assertEqual(self.post(mp3(), "sang.exe").get_json()["file"], "sang.mp3")

    def test_same_file_is_reused_and_a_different_one_gets_a_new_name(self):
        self.assertEqual(self.post(mp3(), "sang.mp3").status_code, 201)
        resp = self.post(mp3(), "Sang.mp3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {"file": "sang.mp3", "format": "MP3", "reused": True})
        self.assertEqual(self.post(mp3(frames=5), "sang.mp3").get_json()["file"], "sang-2.mp3")
        self.assertEqual(self.post(mp3(frames=6), "sang.mp3").get_json()["file"], "sang-3.mp3")
        self.assertEqual(self.post(mp3(frames=5), "sang.mp3").get_json(),
                         {"file": "sang-2.mp3", "format": "MP3", "reused": True})
        self.assertEqual(self.files(), ["sang-2.mp3", "sang-3.mp3", "sang.mp3"])
        self.assertEqual((self.media / "sang.mp3").read_bytes(), mp3())  # never overwritten

    def test_refused_uploads(self):
        resp = self.post(b"<?php echo 1; ?>", "song.mp3")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("ikke en lydfil", resp.get_json()["error"])
        self.assertEqual(self.post(b"", "tom.mp3").get_json()["error"], "Filen er tom.")
        self.assertEqual(self.post(mp4((b"soun", b"alac")), "a.m4a").status_code, 400)
        self.assertEqual(self.client.post("/lag/lyd", data={}).status_code, 400)
        self.assertEqual(self.client.post("/lag/lyd", json={"file": "x"}).status_code, 400)
        self.assertEqual(self.files(), [])

    def test_size_limit(self):
        with mock.patch("app.MAX_AUDIO_UPLOAD", 2000):
            self.assertEqual(self.post(mp3(frames=4), "liten.mp3").status_code, 201)  # 1668 bytes
            resp = self.post(mp3(frames=5), "stor.mp3")  # 2085 bytes: past the limit, inside the form's slack
            self.assertEqual(resp.status_code, 413)
            self.assertIn("for stor", resp.get_json()["error"])
            resp = self.post(mp3(frames=500), "enorm.mp3")  # past the request limit: refused before it is read
            self.assertEqual(resp.status_code, 413)
        self.assertEqual(self.files(), ["liten.mp3"])
        # The bigger limit is for this route only: other posts still stop at 1 MB.
        big = mp3(frames=3000)  # 1.25 MB
        self.assertEqual(self.post(big, "stor.mp3").status_code, 201)
        self.assertEqual(self.post(big, "q.json", url="/last-opp", field="files").status_code, 413)

    def test_needs_password_and_same_origin(self):
        client = self.make_client(password="s3cret")
        auth = {"Authorization": "Basic " + base64.b64encode(b":s3cret").decode()}
        self.assertEqual(self.post(mp3(), "a.mp3", client).status_code, 401)
        self.assertEqual(self.post(mp3(), "a.mp3", client, headers={**auth, "Origin": "https://evil.example"}).status_code,
                         403)
        self.assertFalse(self.media.exists())
        self.assertEqual(self.post(mp3(), "a.mp3", client, headers={**auth, "Origin": "http://localhost"}).status_code,
                         201)

    def test_builder_page_knows_where_to_upload(self):
        page = self.client.get("/lag").get_data(as_text=True)
        self.assertIn('data-upload-url="/lag/lyd"', page)
        self.assertIn('data-audio-max-mb="25"', page)



def music_quiz(title, *files):
    return {"title": title, "categories": [{"name": "Låter", "questions": [
        {"value": 100 * (i + 1), "question": "Hvem?", "answer": "a-ha", "audio": f} for i, f in enumerate(files)]}]}


class MediaPageTest(AppTestCase):
    """/lydfiler: the list of audio files, and renaming and deleting them."""

    def setUp(self):
        super().setUp()
        for name, frames in (("a.mp3", 4), ("b.mp3", 5), ("c.mp3", 6)):
            self.post(mp3(frames), name)
        (self.media / "80s").mkdir()
        (self.media / "80s" / "d.mp3").write_bytes(mp3(7))
        (self.media / "notes.txt").write_text("not audio")
        (self.media / ".upload-abc").write_bytes(mp3())  # an upload in progress
        self.save(music_quiz("Fredag", "a.mp3", "80s/d.mp3"))
        self.save(music_quiz("Lørdag", "a.mp3"))

    def save(self, quiz):
        resp = self.client.post("/api/quizzes", json=quiz)
        self.assertIn(resp.status_code, (200, 201), resp.get_json())

    def quiz_audio(self, slug):
        quiz = self.client.get(f"/api/quizzes/{slug}").get_json()
        return [q.get("audio") for c in quiz["categories"] for q in c["questions"]]

    def page(self):
        resp = self.client.get("/lydfiler")
        self.assertEqual(resp.status_code, 200)
        return resp.get_data(as_text=True)

    def test_lists_audio_files_with_what_uses_them(self):
        page = self.page()
        for name in ("a.mp3", "b.mp3", "c.mp3", "80s/d.mp3"):
            self.assertIn(f'value="{name}"', page)
        self.assertNotIn("notes.txt", page)
        self.assertNotIn(".upload-abc", page)
        self.assertIn('href="/lag/fredag">Fredag</a>', page)
        self.assertIn('href="/lag/lordag">Lørdag</a>', page)
        self.assertEqual(page.count("Ikke i bruk"), 2)  # b and c
        self.assertIn('src="/lag/lyd/80s/d.mp3"', page)
        self.assertIn("4 filer", page)
        self.assertIn("Slett ubrukte filer (2,", page)
        self.assertIn('href="/lydfiler"', self.client.get("/admin").get_data(as_text=True))

    def test_delete_only_unused_files(self):
        resp = self.client.post("/lydfiler/slett", data={"name": "b.mp3"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Slettet «b.mp3»", resp.get_data(as_text=True))
        self.assertFalse((self.media / "b.mp3").exists())
        resp = self.client.post("/lydfiler/slett", data={"name": "a.mp3"})
        self.assertEqual(resp.status_code, 409)
        self.assertTrue((self.media / "a.mp3").exists())
        for name in ("b.mp3", "../kviss.db", "notes.txt", ".upload-abc", ""):
            self.assertEqual(self.client.post("/lydfiler/slett", data={"name": name}).status_code, 404, name)
        self.assertTrue((self.tmp / "kviss.db").exists())
        self.assertTrue((self.media / "notes.txt").exists())

    def test_the_game_on_the_tv_keeps_its_files(self):
        self.client.post("/nytt/lordag", data={"players": "A\nB"})
        self.client.delete("/api/quizzes/lordag")
        self.client.delete("/api/quizzes/fredag")
        # No stored quiz uses a.mp3 now, but the game on the TV has its own copy of the quiz that does.
        self.assertIn("På TV-en nå", self.page())
        self.assertEqual(self.client.post("/lydfiler/slett", data={"name": "a.mp3"}).status_code, 409)
        resp = self.client.post("/lydfiler/nytt-navn", data={"name": "a.mp3", "new_name": "x"})
        self.assertEqual(resp.status_code, 409)
        resp = self.client.post("/lydfiler/rydd", data={"confirm": "yes", "names": ["a.mp3", "b.mp3"]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue((self.media / "a.mp3").exists())
        self.assertFalse((self.media / "b.mp3").exists())
        resp = self.client.get("/media/a.mp3")
        self.assertEqual(resp.status_code, 200)
        resp.close()

    def test_rename_updates_the_quizzes_that_use_the_file(self):
        resp = self.client.post("/lydfiler/nytt-navn", data={"name": "a.mp3", "new_name": "A-ha – Take On Me"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("heter nå «a-ha-take-on-me.mp3». Oppdaterte Fredag, Lørdag.", resp.get_data(as_text=True))
        self.assertEqual((self.media / "a-ha-take-on-me.mp3").read_bytes(), mp3(4))
        self.assertFalse((self.media / "a.mp3").exists())
        self.assertEqual(self.quiz_audio("fredag"), ["a-ha-take-on-me.mp3", "80s/d.mp3"])
        self.assertEqual(self.quiz_audio("lordag"), ["a-ha-take-on-me.mp3"])
        # A sub-folder stays; the quizzes still pass the checks a save makes.
        resp = self.client.post("/lydfiler/nytt-navn", data={"name": "80s/d.mp3", "new_name": "Hit"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.quiz_audio("fredag"), ["a-ha-take-on-me.mp3", "80s/hit.mp3"])
        quiz = self.client.get("/api/quizzes/fredag").get_json()
        self.assertEqual(self.client.post("/api/quizzes", json=quiz).status_code, 200)

    def test_rename_refusals(self):
        def rename(name, new_name):
            return self.client.post("/lydfiler/nytt-navn", data={"name": name, "new_name": new_name})
        resp = rename("b.mp3", "C")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("allerede en fil som heter «c.mp3»", resp.get_data(as_text=True))
        self.assertEqual(rename("b.mp3", "♪").status_code, 400)
        self.assertIn("har allerede det navnet", rename("b.mp3", "B").get_data(as_text=True))
        self.assertEqual(rename("missing.mp3", "x").status_code, 404)
        self.assertEqual(rename("b.mp3", "../../evil").status_code, 200)  # cleaned to a plain name
        self.assertTrue((self.media / "evil.mp3").exists())
        self.assertEqual((self.media / "c.mp3").read_bytes(), mp3(6))

    def test_delete_unused_asks_first_and_keeps_files_put_to_use_meanwhile(self):
        resp = self.client.post("/lydfiler/rydd")
        page = resp.get_data(as_text=True)
        self.assertIn("Slette ubrukte filer?", page)
        self.assertIn('name="names" value="b.mp3"', page)
        self.assertIn('name="names" value="c.mp3"', page)
        self.assertNotIn('name="names" value="a.mp3"', page)
        self.assertTrue((self.media / "b.mp3").exists())
        self.save(music_quiz("Søndag", "c.mp3"))  # c is used before the host confirms
        resp = self.client.post("/lydfiler/rydd", data={"confirm": "yes", "names": ["a.mp3", "b.mp3", "c.mp3"]})
        self.assertIn("Slettet 1 fil", resp.get_data(as_text=True))
        self.assertEqual(sorted(p.name for p in self.media.glob("*.mp3")), ["a.mp3", "c.mp3"])
        self.assertIn("Ingenting å rydde", self.client.post("/lydfiler/rydd").get_data(as_text=True))

    def test_upload_without_javascript(self):
        boundary, body = encode_multipart({"files": [FileStorage(io.BytesIO(mp3(8)), "Ny sang.mp3"),
                                                     FileStorage(io.BytesIO(b"nope"), "x.mp3")]})
        resp = self.client.post("/lydfiler", data=body, content_type=f"multipart/form-data; boundary={boundary}")
        self.assertEqual(resp.status_code, 200)
        page = resp.get_data(as_text=True)
        self.assertIn("ny-sang.mp3", page)
        self.assertIn("ikke en lydfil", page)
        self.assertEqual(self.client.post("/lydfiler", data={}).status_code, 400)

    def test_new_posts_need_password_and_same_origin(self):
        client = self.make_client(password="s3cret")
        auth = {"Authorization": "Basic " + base64.b64encode(b":s3cret").decode()}
        self.assertEqual(client.get("/lydfiler").status_code, 401)
        for url in ("/lydfiler", "/lydfiler/slett", "/lydfiler/nytt-navn", "/lydfiler/rydd"):
            self.assertEqual(client.post(url, data={"name": "b.mp3"}).status_code, 401, url)
            resp = client.post(url, data={"name": "b.mp3", "confirm": "yes", "names": "b.mp3"},
                               headers={**auth, "Origin": "https://evil.example"})
            self.assertEqual(resp.status_code, 403, url)
        self.assertTrue((self.media / "b.mp3").exists())


class QuotaTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.client = create_app(self.tmp / "kviss.db", media_dir=self.media, seed=[], password="",
                                 media_max_mb=1).test_client()

    def test_uploads_stop_at_the_limit(self):
        big = mp3(frames=2000)  # 834,000 bytes = 814 kB
        self.assertEqual(self.post(big, "a.mp3").status_code, 201)
        self.assertEqual(self.post(big, "a.mp3").status_code, 200)  # the same file again takes no space
        resp = self.post(mp3(frames=1000), "b.mp3")  # 417 kB more: past 1 MB
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Lydmappen er full: 814 kB av 1,0 MB er brukt", resp.get_json()["error"])
        self.assertEqual(self.files(), ["a.mp3"])
        self.assertEqual(self.post(mp3(frames=300), "c.mp3").status_code, 201)  # 125 kB still fits
        self.assertIn("av 1,0 MB brukt", self.client.get("/lydfiler").get_data(as_text=True))

    def test_setting(self):
        from app import default_media_max_mb
        for value, expected in (("", None), ("500", 500), (" 20 ", 20)):
            with mock.patch.dict("os.environ", {"KVISS_MEDIA_MAX_MB": value}):
                self.assertEqual(default_media_max_mb(), expected)
        for value in ("0", "-5", "1.5", "lots"):
            with mock.patch.dict("os.environ", {"KVISS_MEDIA_MAX_MB": value}), self.assertRaises(ValueError):
                default_media_max_mb()


if __name__ == "__main__":
    unittest.main()
