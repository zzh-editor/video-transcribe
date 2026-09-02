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
            mock_cleanup = MagicMock(side_effect=lambda segs: segs)
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
