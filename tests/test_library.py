import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event
from unittest.mock import Mock, patch

from support import make_video

from shortspublisher.library import (
    SCHEMA_VERSION,
    Library,
    LibraryError,
    LibraryService,
    canonical,
    source_status,
)
from shortspublisher.media import Cancelled, InspectionError, Inspector, Media, parse_metadata
from shortspublisher.readiness import evaluate

MEDIA = Media(3, 1, 2, 160, 240, 2 / 3, 24, "h264", ("aac",), 48, 0)


class RulesTests(unittest.TestCase):
    def test_duration_boundary_and_shape(self):
        self.assertEqual(
            evaluate(replace(MEDIA, duration=180, aspect=1)).status, "Passes implemented checks"
        )
        self.assertIn("3-minute", str(evaluate(replace(MEDIA, duration=180.001)).messages))
        self.assertIn("Landscape", str(evaluate(replace(MEDIA, aspect=16 / 9)).messages))

    def test_optional_metadata_is_warning_not_invalid(self):
        result = evaluate(replace(MEDIA, fps=None, audio_codecs=()))
        self.assertIn("Valid video", result.status)
        self.assertIn("No audio", str(result.messages))
        self.assertIn("Frame rate", str(result.messages))

    def test_rotation_and_pixel_aspect(self):
        data = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 240,
                    "height": 160,
                    "nb_read_frames": "48",
                    "duration": "2",
                    "avg_frame_rate": "24/1",
                    "sample_aspect_ratio": "1:1",
                    "side_data_list": [{"rotation": -90}],
                }
            ]
        }
        result = parse_metadata(data, 123, 1)
        self.assertAlmostEqual(result.aspect, 2 / 3)
        data["streams"][0]["sample_aspect_ratio"] = "2:1"
        self.assertAlmostEqual(parse_metadata(data, 123, 1).aspect, 1 / 3)

    def test_invalid_duration_or_frames_rejected(self):
        base = {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 160,
            "height": 240,
            "nb_read_frames": "48",
            "duration": "2",
        }
        for key, value in (
            ("duration", "nan"),
            ("duration", "inf"),
            ("duration", "0"),
            ("nb_read_frames", "0"),
            ("width", 0),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(InspectionError):
                parse_metadata({"streams": [{**base, key: value}]}, 3, 1)

    def test_malformed_metadata_rejected(self):
        for data in (
            None,
            {},
            {"streams": []},
            {"streams": [None]},
            {"streams": [{"codec_type": "audio"}]},
            {"streams": [{"codec_type": "video", "width": 1}]},
        ):
            with self.subTest(data=data), self.assertRaises(InspectionError):
                parse_metadata(data, 3, 1)


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = Library(self.root / "data" / "library.sqlite3")
        self.library.initialize()

    def test_persistence_and_safe_remove(self):
        source = self.root / "source.mp4"
        source.write_bytes(b"original")
        video, created = self.library.save(str(source), MEDIA)
        self.assertTrue(created)
        reopened = Library(self.library.path)
        reopened.initialize()
        self.assertEqual(reopened.all(), [video])
        reopened.remove(video.id)
        self.assertEqual(reopened.all(), [])
        self.assertEqual(source.read_bytes(), b"original")
        with sqlite3.connect(self.library.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)

    def test_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.library.save("/same", MEDIA), range(2)))
        self.assertEqual(len(self.library.all()), 1)
        self.assertEqual(sum(created for _, created in results), 1)

    def test_canonical_symlink_and_relative_path(self):
        source = self.root / "sp ace 雪.mp4"
        source.write_bytes(b"source")
        alias = self.root / "alias.mp4"
        alias.symlink_to(source)
        self.assertEqual(canonical(alias), canonical(source))
        self.assertEqual(canonical(source.parent / "." / source.name), canonical(source))

    def test_future_schema_preserved(self):
        with sqlite3.connect(self.library.path) as db:
            db.execute("PRAGMA user_version=99")
        original = self.library.path.read_bytes()
        with self.assertRaises(LibraryError):
            self.library.initialize()
        self.assertEqual(self.library.path.read_bytes(), original)

    def test_corrupt_database_preserved(self):
        self.library.path.write_bytes(b"not a sqlite database")
        service = LibraryService(self.library, Inspector())
        with self.assertRaisesRegex(LibraryError, "not reset"):
            service.execute("load", None, Event())
        self.assertEqual(self.library.path.read_bytes(), b"not a sqlite database")

    def test_corrupt_record_is_reported(self):
        video, _ = self.library.save("/some/video", MEDIA)
        with sqlite3.connect(self.library.path) as db:
            db.execute("UPDATE videos SET media = ?", ("broken",))
        with self.assertRaisesRegex(LibraryError, "preserved"):
            self.library.get(video.id)

    def test_locked_database_reported_and_recovers(self):
        with sqlite3.connect(self.library.path) as db:
            db.execute("BEGIN EXCLUSIVE")
            with self.assertRaises(LibraryError):
                LibraryService(self.library, Inspector()).execute("load", None, Event())
        self.assertEqual(
            LibraryService(self.library, Inspector()).execute("load", None, Event())["videos"], []
        )


class ProcessFailureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "video file 雪.mp4"
        self.path.write_bytes(b"test")

    def test_missing_ffprobe_and_failed_executable(self):
        with (
            patch("shortspublisher.media.shutil.which", return_value=None),
            self.assertRaisesRegex(InspectionError, "brew install"),
        ):
            Inspector().inspect(self.path, Event())
        with self.assertRaisesRegex(InspectionError, "could not start"):
            Inspector("/does/not/exist/ffprobe").inspect(self.path, Event())

    def test_empty_missing_and_unreadable(self):
        with self.assertRaises(InspectionError):
            Inspector().inspect(self.path.parent / "missing", Event())
        self.path.write_bytes(b"")
        with self.assertRaises(InspectionError):
            Inspector().inspect(self.path, Event())
        self.path.write_bytes(b"test")
        with (
            patch.object(Path, "open", side_effect=PermissionError),
            self.assertRaises(InspectionError),
        ):
            Inspector().inspect(self.path, Event())

    def fake_process(self, output=b"{}", error=b""):
        process = Mock()
        process.communicate.return_value = (output, error)
        process.poll.return_value = 0
        process.returncode = 0
        return process

    def test_invalid_json_and_stream_output(self):
        for output in (b"not json", b"{}", b'{"streams": []}'):
            with (
                patch(
                    "shortspublisher.media.subprocess.Popen", return_value=self.fake_process(output)
                ),
                self.assertRaises(InspectionError),
            ):
                Inspector("ffprobe").inspect(self.path, Event())

    def test_decoder_errors_rejected_even_with_zero_exit(self):
        with (
            patch(
                "shortspublisher.media.subprocess.Popen",
                return_value=self.fake_process(error=b"damaged frame"),
            ),
            self.assertRaisesRegex(InspectionError, "damaged frame"),
        ):
            Inspector("ffprobe").inspect(self.path, Event())

    def test_timeout_reaps_process(self):
        process = self.fake_process()
        process.poll.return_value = None
        with (
            patch("shortspublisher.media.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(InspectionError, "timed out"),
        ):
            Inspector("ffprobe", timeout=-1).inspect(self.path, Event())
        process.kill.assert_called_once()
        process.communicate.assert_called_once()

    def test_cancel_during_inspection_reaps_process(self):
        cancel = Event()
        process = self.fake_process()
        process.poll.return_value = None

        def interrupted(*args, **kwargs):
            if kwargs:
                cancel.set()
                raise subprocess.TimeoutExpired("ffprobe", 0.1)
            return (b"", b"")

        process.communicate.side_effect = interrupted
        with (
            patch("shortspublisher.media.subprocess.Popen", return_value=process),
            self.assertRaises(Cancelled),
        ):
            Inspector("ffprobe").inspect(self.path, cancel)
        process.kill.assert_called_once()

    def test_disappearing_source(self):
        process = self.fake_process()

        def disappear(*args, **kwargs):
            self.path.unlink(missing_ok=True)
            return b"{}", b""

        process.communicate.side_effect = disappear
        with (
            patch("shortspublisher.media.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(InspectionError, "disappeared"),
        ):
            Inspector("ffprobe").inspect(self.path, Event())

    def test_source_changed_during_inspection(self):
        process = self.fake_process()

        def change(*args, **kwargs):
            self.path.write_bytes(b"changed while reading")
            return b"{}", b""

        process.communicate.side_effect = change
        with (
            patch("shortspublisher.media.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(InspectionError, "changed during"),
        ):
            Inspector("ffprobe").inspect(self.path, Event())

    def test_nonzero_exit_rejected(self):
        process = self.fake_process()
        process.returncode = 1
        with (
            patch("shortspublisher.media.subprocess.Popen", return_value=process),
            self.assertRaisesRegex(InspectionError, "Unsupported or damaged"),
        ):
            Inspector("ffprobe").inspect(self.path, Event())

    def test_safe_argument_list(self):
        process = self.fake_process()
        with (
            patch("shortspublisher.media.subprocess.Popen", return_value=process) as popen,
            self.assertRaises(InspectionError),
        ):
            Inspector("ffprobe").inspect(self.path, Event())
        command = popen.call_args.args[0]
        self.assertIsInstance(command, list)
        self.assertEqual(command[-1], str(self.path))
        self.assertEqual(command[command.index("-protocol_whitelist") + 1], "file,pipe")
        self.assertNotIn("shell", popen.call_args.kwargs)


class RealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixtures.cleanup)
        cls.fixture = Path(cls.fixtures.name) / "fixture.mp4"
        make_video(cls.fixture)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / ("long filename 雪 " + "a" * 130 + ".mp4")
        shutil.copyfile(self.fixture, self.source)
        self.before = hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.library = Library(self.root / "data" / "library.sqlite3")
        self.service = LibraryService(self.library, Inspector())

    def import_video(self):
        return self.service.execute("import", str(self.source), Event())["selected"]

    def test_real_import_metadata_and_restart(self):
        video = self.import_video()
        self.assertEqual(
            (video.media.width, video.media.height, video.media.frames), (160, 240, 48)
        )
        self.assertAlmostEqual(video.media.duration, 2, places=2)
        self.assertAlmostEqual(video.media.fps, 24)
        self.assertEqual(video.media.video_codec, "h264")
        self.assertEqual(video.media.audio_codecs, ("aac",))
        self.assertEqual(video.media.size, self.source.stat().st_size)
        self.assertEqual(evaluate(video.media).status, "Passes implemented checks")
        reopened = LibraryService(Library(self.library.path), Inspector())
        self.assertEqual(reopened.execute("load", None, Event())["videos"], [video])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_duplicate_symlink_import_avoids_inspection(self):
        video = self.import_video()
        alias = self.root / "alias.mp4"
        alias.symlink_to(self.source)
        with patch.object(
            self.service.inspector, "inspect", side_effect=AssertionError("Should not reinspect")
        ):
            result = self.service.execute("import", str(alias), Event())
        self.assertEqual(result["selected"].id, video.id)
        self.assertEqual(len(result["videos"]), 1)

    def test_missing_source_refresh_failure_and_recovery(self):
        video = self.import_video()
        original = self.source.read_bytes()
        self.source.unlink()
        result = self.service.execute("select", video.id, Event())
        self.assertIn("missing", result["source_status"])
        failed = self.service.execute("refresh", video.id, Event())["selected"]
        self.assertIn("missing", failed.error)
        self.assertEqual(failed.media, video.media)
        self.source.write_bytes(original)
        fixed = self.service.execute("refresh", video.id, Event())["selected"]
        self.assertEqual(fixed.error, "")
        self.assertEqual(fixed.imported_at, video.imported_at)
        self.assertEqual(source_status(fixed), "")

    def test_refresh_changed_video(self):
        video = self.import_video()
        make_video(self.source, audio=False, size="240x160")
        self.assertIn("changed", source_status(video))
        updated = self.service.execute("refresh", video.id, Event())["selected"]
        self.assertEqual(updated.media.width, 240)
        self.assertEqual(updated.media.audio_codecs, ())
        self.assertIn("Landscape", str(evaluate(updated.media).messages))
        self.assertEqual(updated.id, video.id)

    def test_corrupt_and_truncated_media_leave_no_record(self):
        self.source.write_bytes(b"this is not a video")
        with self.assertRaises(InspectionError):
            self.import_video()
        self.assertEqual(self.library.all(), [])
        self.source.write_bytes(self.fixture.read_bytes()[:100])
        with self.assertRaises(InspectionError):
            self.import_video()
        self.assertEqual(self.library.all(), [])

    def test_mov_and_webm(self):
        for extension in (".mov", ".webm"):
            path = self.root / ("silent" + extension)
            make_video(path, audio=False)
            video = self.service.execute("import", str(path), Event())["selected"]
            self.assertEqual(video.media.audio_codecs, ())
            self.assertIn("No audio", str(evaluate(video.media).messages))

    def test_remove_leaves_original_hash_unchanged(self):
        video = self.import_video()
        result = self.service.execute("remove", video.id, Event())
        self.assertEqual(result["videos"], [])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.before)

    def test_pre_cancelled_import_does_not_create_record(self):
        cancel = Event()
        cancel.set()
        with self.assertRaises(Cancelled):
            self.service.execute("import", str(self.source), cancel)
        self.assertFalse(self.library.path.exists())

    def test_metadata_not_extension_determines_validity(self):
        renamed = self.root / "actual-video.txt"
        shutil.copyfile(self.source, renamed)
        self.assertEqual(
            self.service.execute("import", str(renamed), Event())["selected"].media.frames, 48
        )
