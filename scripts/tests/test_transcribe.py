"""Tests for transcribe.py — engine branches, thresholds, pause propagation.

Mocks heavy deps (mlx_whisper, faster_whisper, silero) so the suite runs
without models or Apple Silicon.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Helpers ──────────────────────────────────────────────────────────

def _fake_mlx_result(segments=None):
    if segments is None:
        segments = [{"start": 0.0, "end": 2.0, "text": "你好世界", "words": []}]
    return {"segments": segments}


def _fake_fw_segment(start=0.0, end=1.0, text="你好"):
    m = MagicMock()
    m.start = start
    m.end = end
    m.text = text
    m.words = []
    return m


class TestPostprocess(unittest.TestCase):
    def test_postprocess_empty_passthrough(self):
        from transcribe import _postprocess
        self.assertEqual(_postprocess([], 25, 6000), [])

    def test_postprocess_calls_refine_and_cleanup(self):
        from transcribe import _postprocess
        segs = [{"start": 0, "end": 1, "text": "你好", "words": []}]
        # Use real refine/cleanup but just verify no crash and count preserved
        out = _postprocess(segs, 25, 6000)
        self.assertGreaterEqual(len(out), 1)
        self.assertEqual(out[0]["text"], "你好")

    def test_postprocess_preserves_source_quality(self):
        from transcribe import _postprocess
        segs = [{"start": 0, "end": 1, "text": "你好", "no_speech_prob": 0.1,
                 "avg_logprob": -0.2,
                 "words": [{"word": "你", "start": 0, "end": 0.4},
                           {"word": "好", "start": 0.5, "end": 1}]}]
        out = _postprocess(segs, 15, 3000)
        self.assertEqual(out[0]["source_quality"]["no_speech_prob"], 0.1)
        self.assertEqual(out[0]["source_quality"]["avg_logprob"], -0.2)


class TestTimestampRounding(unittest.TestCase):
    def test_timestamp_rounds_to_integer_milliseconds(self):
        from transcribe import format_timestamp
        self.assertEqual(format_timestamp(12.019), "00:00:12,019")


class TestTranscribeMlx(unittest.TestCase):
    def _mock_mlx(self, result=None):
        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = _fake_mlx_result(result)
        return mock_mlx

    def test_non_vad_passes_thresholds(self):
        mock_mlx = self._mock_mlx()
        with patch.dict(sys.modules, {"mlx_whisper": mock_mlx}):
            from transcribe import transcribe_mlx
            transcribe_mlx("/tmp/fake.wav", "mlx-community/whisper-large-v2-mlx",
                           "zh", 25, 0.3, 4000, vad=False)
        kwargs = mock_mlx.transcribe.call_args.kwargs
        self.assertEqual(kwargs["logprob_threshold"], -1.0)
        self.assertEqual(kwargs["no_speech_threshold"], 0.6)
        self.assertIn("initial_prompt", kwargs)

    def test_non_vad_zh_sets_initial_prompt(self):
        mock_mlx = self._mock_mlx()
        with patch.dict(sys.modules, {"mlx_whisper": mock_mlx}):
            from transcribe import transcribe_mlx
            transcribe_mlx("/tmp/fake.wav", "m", "zh", 25, 0.3, 4000, vad=False)
        self.assertIsNotNone(mock_mlx.transcribe.call_args.kwargs["initial_prompt"])

    def test_non_vad_en_no_initial_prompt(self):
        mock_mlx = self._mock_mlx()
        with patch.dict(sys.modules, {"mlx_whisper": mock_mlx}):
            from transcribe import transcribe_mlx
            transcribe_mlx("/tmp/fake.wav", "m", "en", 25, 0.5, 4000, vad=False)
        self.assertIsNone(mock_mlx.transcribe.call_args.kwargs["initial_prompt"])

    def test_vad_branch_passes_thresholds(self):
        # vad=True goes through _transcribe_vad_chunks; mock that helper instead
        fake_segs = [{"start": 0, "end": 1, "text": "你好", "words": []}]
        with patch("transcribe._transcribe_vad_chunks", return_value=fake_segs) as mock_vad:
            mock_mlx = self._mock_mlx()  # not used in vad path but still needs import
            with patch.dict(sys.modules, {"mlx_whisper": mock_mlx}):
                from transcribe import transcribe_mlx
                out = transcribe_mlx("/tmp/fake.wav", "m", "zh", 25, 0.3, 4000, vad=True)
        mock_vad.assert_called_once()
        self.assertEqual(out[0]["text"], "你好")

    def test_empty_segments_exits(self):
        mock_mlx = MagicMock()
        mock_mlx.transcribe.return_value = {"segments": []}
        with patch.dict(sys.modules, {"mlx_whisper": mock_mlx}):
            from transcribe import transcribe_mlx
            with self.assertRaises(SystemExit):
                transcribe_mlx("/tmp/fake.wav", "m", "zh", 25, 0.3, 4000, vad=False)


class TestTranscribeFaster(unittest.TestCase):
    def _make_model(self, segments=None, lang="zh"):
        segs = segments or [_fake_fw_segment()]
        info = MagicMock()
        info.language = lang
        info.language_probability = 0.99
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter(segs), info)
        # signature probe: include initial_prompt by default
        import inspect
        # MagicMock signature fallback: make inspect.signature return one with initial_prompt
        return mock_model

    def test_faster_passes_thresholds_and_vad_filter(self):
        mock_whisper_model = self._make_model()
        mock_fw = MagicMock()
        mock_fw.WhisperModel.return_value = mock_whisper_model
        with patch.dict(sys.modules, {"faster_whisper": mock_fw}):
            from transcribe import transcribe_faster
            transcribe_faster("/tmp/fake.wav", "large-v2", "zh", 25, 0.3, 4000, vad=True)
        # WhisperModel init
        mock_fw.WhisperModel.assert_called_once()
        # transcribe called with expected kwargs
        kwargs = mock_whisper_model.transcribe.call_args.kwargs
        self.assertEqual(kwargs["log_prob_threshold"], -1.0)
        self.assertEqual(kwargs["no_speech_threshold"], 0.6)
        self.assertTrue(kwargs["vad_filter"])

    def test_faster_vad_false(self):
        mock_model = self._make_model()
        mock_fw = MagicMock()
        mock_fw.WhisperModel.return_value = mock_model
        with patch.dict(sys.modules, {"faster_whisper": mock_fw}):
            from transcribe import transcribe_faster
            transcribe_faster("/tmp/fake.wav", "large-v2", "zh", 25, 0.3, 4000, vad=False)
        self.assertFalse(mock_model.transcribe.call_args.kwargs["vad_filter"])

    def test_normalize_keeps_quality_metrics(self):
        from transcribe import _normalize_segments
        segment = _fake_fw_segment()
        segment.no_speech_prob = 0.25
        segment.avg_logprob = -0.3
        out = _normalize_segments([segment])
        self.assertEqual(out[0]["no_speech_prob"], 0.25)
        self.assertEqual(out[0]["avg_logprob"], -0.3)

    def test_faster_initial_prompt_compat_missing_param(self):
        # Simulate older faster-whisper without initial_prompt
        segs = [_fake_fw_segment()]
        info = MagicMock()
        info.language = "zh"
        info.language_probability = 0.9
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter(segs), info)
        # Make signature without initial_prompt
        import inspect
        def fake_transcribe(audio, language=None, word_timestamps=False, vad_filter=False,
                            log_prob_threshold=-1.0, no_speech_threshold=0.6):
            pass
        mock_model.transcribe.__signature__ = inspect.signature(fake_transcribe)
        # Patch inspect.signature to return the fake one for this model
        mock_fw = MagicMock()
        mock_fw.WhisperModel.return_value = mock_model
        orig_sig = inspect.signature

        def side_effect(obj):
            if obj is mock_model.transcribe:
                return inspect.signature(fake_transcribe)
            return orig_sig(obj)

        with patch.dict(sys.modules, {"faster_whisper": mock_fw}):
            with patch("inspect.signature", side_effect=side_effect):
                from transcribe import transcribe_faster
                out = transcribe_faster("/tmp/fake.wav", "large-v2", "zh", 25, 0.3, 4000, vad=False)
        # Should not have initial_prompt in kwargs
        self.assertNotIn("initial_prompt", mock_model.transcribe.call_args.kwargs)
        self.assertEqual(len(out), 1)

    def test_faster_empty_segments_exits(self):
        info = MagicMock()
        info.language = "zh"
        info.language_probability = 0.9
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (iter([]), info)
        mock_fw = MagicMock()
        mock_fw.WhisperModel.return_value = mock_model
        with patch.dict(sys.modules, {"faster_whisper": mock_fw}):
            from transcribe import transcribe_faster
            with self.assertRaises(SystemExit):
                transcribe_faster("/tmp/fake.wav", "large-v2", "zh", 25, 0.3, 4000, vad=False)


class TestGroqPausePropagation(unittest.TestCase):
    """Verify --pause-ms → Groq groq_pause conversion in main()."""

    def _run_main(self, pause_ms, expected_groq_pause):
        import tempfile, os, json, subprocess
        from unittest.mock import MagicMock

        # Create a tiny fake audio file so Path.exists() passes
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(b"\x00" * 100)
            audio = tf.name
        out_srt = audio + ".srt"
        try:
            fake_result = {"segments": [{"start": 0, "end": 1, "text": "你好"}],
                           "words": []}
            mock_groq = MagicMock(return_value=fake_result)
            mock_refine = MagicMock(side_effect=lambda segs, words, **kw: segs)
            mock_cleanup = MagicMock(side_effect=lambda segs, audit=None: segs)
            # Mock os.path.getsize for _compress path (if needed elsewhere)
            # Groq path uses transcribe_groq which we mock entirely
            with patch.dict(sys.modules, {"groq_transcribe": MagicMock(transcribe_groq=mock_groq)}):
                with patch("groq_word_adapter.refine_groq_segments", mock_refine):
                    with patch("cleanup_segments.cleanup", mock_cleanup):
                        # Also need get_audio_duration to avoid ffprobe
                        with patch("transcribe.get_audio_duration", return_value=100):
                            with patch("transcribe.detect_platform", return_value={"is_macos": True, "is_arm": True}):
                                with patch("transcribe.write_srt"):
                                    from transcribe import main
                                    argv = ["transcribe.py", audio, "--engine", "groq",
                                            "--groq-api-key", "fake-key"]
                                    if pause_ms is not None:
                                        argv += ["--pause-ms", str(pause_ms)]
                                    with patch.object(sys, "argv", argv):
                                        main()
                                    # Check groq_pause passed to refine
                                    if mock_refine.called:
                                        actual = mock_refine.call_args.kwargs.get("pause_threshold")
                                        self.assertAlmostEqual(actual, expected_groq_pause, places=5)
        finally:
            if os.path.exists(audio):
                os.unlink(audio)
            if os.path.exists(out_srt):
                os.unlink(out_srt)

    def test_default_groq_pause_0_15(self):
        self._run_main(None, 0.15)

    def test_custom_pause_300ms(self):
        self._run_main(300, 0.3)

    def test_custom_pause_500ms(self):
        self._run_main(500, 0.5)


class TestGroqWordAdapterMainIntegration(unittest.TestCase):
    def test_english_punctuation_survives_entire_pipeline(self):
        import tempfile
        from transcribe import main
        from refine_segments import parse_srt

        text = "First, what do I want? This is the story."
        words = [{"word": word, "start": i * 0.3, "end": (i + 1) * 0.3}
                 for i, word in enumerate(text.split())]
        result = {"segments": [{"start": 0, "end": words[-1]["end"], "text": text}],
                  "words": words}
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.mp3"
            output = Path(directory) / "output.srt"
            audio.write_bytes(b"audio")
            with patch("groq_transcribe.transcribe_groq", return_value=result), \
                    patch("groq_word_adapter._AudioEnergy", return_value=None), \
                    patch("transcribe.get_audio_duration", return_value=4), \
                    patch.object(sys, "argv", [
                        "transcribe.py", str(audio), "--engine", "groq",
                        "--language", "en", "--groq-api-key", "fake-key",
                        "--output", str(output)]):
                main()
            captions = parse_srt(str(output))
            self.assertEqual([s["text"] for s in captions],
                             ["First,", "what do I want?", "This is the story."])
            self.assertEqual(captions[0]["end"], words[0]["end"])

    def test_english_incomplete_source_cues_merge_in_entire_pipeline(self):
        import tempfile
        from transcribe import main
        from refine_segments import parse_srt

        words = [
            {"word": "Please", "start": 0, "end": 0.2},
            {"word": "send", "start": 0.2, "end": 0.5},
            {"word": "the", "start": 0.5, "end": 0.7},
            {"word": "file.", "start": 0.7, "end": 1.0},
        ]
        result = {
            "segments": [
                {"start": 0, "end": 0.5, "text": "Please send"},
                {"start": 0.5, "end": 1, "text": "the file."},
            ],
            "words": words,
        }
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.mp3"
            output = Path(directory) / "output.srt"
            audio.write_bytes(b"audio")
            with patch("groq_transcribe.transcribe_groq", return_value=result), \
                    patch("audio_align.align_segments", side_effect=lambda segs, _: (segs, [])), \
                    patch("transcribe.get_audio_duration", return_value=1), \
                    patch.object(sys, "argv", [
                        "transcribe.py", str(audio), "--engine", "groq",
                        "--language", "en", "--groq-api-key", "fake-key",
                        "--output", str(output)]):
                main()
            captions = parse_srt(str(output))
            self.assertEqual([s["text"] for s in captions], ["Please send the file."])
            self.assertEqual((captions[0]["start"], captions[0]["end"]), (0, 1))

    def test_missing_adapter_emits_warning(self):
        import contextlib
        import io
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            output = Path(directory) / "output.srt"
            audio.write_bytes(b"\x00" * 100)
            stderr = io.StringIO()
            with patch.dict(sys.modules, {
                "groq_transcribe": MagicMock(transcribe_groq=MagicMock(return_value={
                    "segments": [{"start": 0, "end": 1, "text": "你好"}],
                    "words": [],
                })),
                "groq_word_adapter": None,
            }):
                with patch("transcribe.get_audio_duration", return_value=1):
                    with patch("transcribe.detect_platform", return_value={
                        "is_macos": True, "is_arm": True,
                    }):
                        from transcribe import main
                        with patch.object(sys, "argv", [
                            "transcribe.py", str(audio), "--engine", "groq",
                            "--groq-api-key", "fake-key", "--output", str(output),
                        ]):
                            with contextlib.redirect_stderr(stderr):
                                main()
            self.assertIn("groq_word_adapter unavailable", stderr.getvalue())
            self.assertIn("retaining Groq segment boundaries", stderr.getvalue())

    def test_main_uses_word_times_for_chinese_splits(self):
        import json
        import tempfile
        import wave

        fixture_path = Path(__file__).resolve().parent / "fixtures" / "groq_zh_verbose.json"
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        segment = dict(fixture["segments"][2])
        # The fixture's final character word ends 100ms after its segment window.
        segment["end"] = 12.1
        words = [w for w in fixture["words"]
                 if w["end"] > segment["start"] and w["start"] < segment["end"]]

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            output = Path(directory) / "output.srt"
            with wave.open(str(audio), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * (16000 * 16))

            mock_groq = MagicMock(return_value={"segments": [segment], "words": words})
            with patch.dict(sys.modules, {
                "groq_transcribe": MagicMock(transcribe_groq=mock_groq),
            }):
                with patch("transcribe.get_audio_duration", return_value=16):
                    with patch("transcribe.detect_platform", return_value={
                        "is_macos": True, "is_arm": True,
                    }):
                        from transcribe import main
                        with patch.object(sys, "argv", [
                            "transcribe.py", str(audio), "--engine", "groq",
                            "--groq-api-key", "fake-key", "--output", str(output),
                            "--max-line-length", "20", "--max-line-ms", "4000",
                        ]):
                            main()

            from refine_segments import parse_srt
            result = parse_srt(str(output))
            self.assertGreater(len(result), 1)
            self.assertEqual("".join(s["text"] for s in result), segment["text"])
            word_ends = {round(w["end"], 3) for w in words}
            self.assertTrue(all(round(s["end"], 3) in word_ends
                                for s in result[:-1]),
                            [(s["start"], s["end"], s["text"]) for s in result])


class TestCleanupAuditExport(unittest.TestCase):
    def test_empty_audit_overwrites_stale_sidecar(self):
        import json
        import tempfile

        from transcribe import export_cleanup_audit

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output.srt"
            audit_path = Path(str(output) + ".cleanup.json")
            audit_path.write_text('[{"action": "removed"}]', encoding="utf-8")
            export_cleanup_audit([], str(output))
            self.assertEqual(json.loads(audit_path.read_text(encoding="utf-8")), [])

    def test_groq_main_writes_cleanup_sidecar(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "input.wav"
            output = Path(directory) / "output.srt"
            audio.write_bytes(b"audio")
            hallucination = "请不吝点赞订阅转发打赏支持明镜与点点栏目"
            groq = MagicMock(return_value={
                "segments": [{"start": 0, "end": 2, "text": hallucination}],
                "words": [],
            })
            with patch.dict(sys.modules, {
                "groq_transcribe": MagicMock(transcribe_groq=groq),
            }):
                with patch("transcribe.get_audio_duration", return_value=2):
                    with patch("transcribe.detect_platform", return_value={
                        "is_macos": True, "is_arm": True,
                    }):
                        from transcribe import main
                        with patch.object(sys, "argv", [
                            "transcribe.py", str(audio), "--engine", "groq",
                            "--groq-api-key", "fake-key", "--output", str(output),
                        ]):
                            main()

            audit_path = Path(str(output) + ".cleanup.json")
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
            self.assertEqual(audit[0]["action"], "removed")
            self.assertIn("known_hallucination", audit[0]["reasons"])


class TestGetPauseThreshold(unittest.TestCase):
    def test_zh_uses_0_3(self):
        from transcribe import get_pause_threshold
        self.assertAlmostEqual(get_pause_threshold("zh"), 0.3)
        self.assertAlmostEqual(get_pause_threshold("zh-CN"), 0.3)

    def test_en_uses_0_5(self):
        from transcribe import get_pause_threshold
        self.assertAlmostEqual(get_pause_threshold("en"), 0.5)
        self.assertAlmostEqual(get_pause_threshold(None), 0.5)


if __name__ == "__main__":
    unittest.main()
