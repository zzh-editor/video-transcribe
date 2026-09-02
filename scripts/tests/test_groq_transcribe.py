"""Tests for groq_transcribe.py — API error branches + _compress_audio."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import groq_transcribe


class TestCompressAudioSmallFile(unittest.TestCase):
    def test_under_limit_returns_original(self):
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
            tf.write(b"\x00" * 1024)
            path = tf.name
        try:
            # file is 1KB < 25MB → no conversion
            with patch("groq_transcribe._get_duration") as mock_dur:
                result = groq_transcribe._compress_audio(path, max_size_mb=25)
            self.assertEqual(result, path)
            mock_dur.assert_not_called()
        finally:
            os.unlink(path)


class TestCompressAudioLargeFile(unittest.TestCase):
    def _make_large_file(self, size_bytes):
        tf = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tf.write(b"\x00" * size_bytes)
        tf.close()
        return tf.name

    @patch("groq_transcribe._get_duration", return_value=100.0)
    @patch("groq_transcribe.subprocess.run")
    def test_converts_to_wav_when_wav_fits(self, mock_run, mock_dur):
        path = self._make_large_file(30 * 1024 * 1024)  # 30MB > 25MB
        try:
            # Mock ffmpeg success and mock wav size to be small enough
            mock_run.return_value = MagicMock()
            # Need to mock getsize for tmp_wav
            orig_getsize = os.path.getsize

            def fake_getsize(p):
                if p == path:
                    return 30 * 1024 * 1024
                # tmp_wav → pretend 10MB
                return 10 * 1024 * 1024

            with patch("os.path.getsize", side_effect=fake_getsize):
                with patch("tempfile.mktemp", return_value=path + ".tmp.wav"):
                    result = groq_transcribe._compress_audio(path, max_size_mb=25)
            # Should return the wav path
            self.assertEqual(result, path + ".tmp.wav")
            # Verify ffmpeg was called with wav params
            args = mock_run.call_args[0][0]
            self.assertIn("-ar", args)
            self.assertIn("16000", args)
        finally:
            os.unlink(path)
            tmp = path + ".tmp.wav"
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("groq_transcribe._get_duration", return_value=200.0)
    @patch("groq_transcribe.subprocess.run")
    def test_converts_to_mp3_when_wav_still_large(self, mock_run, mock_dur):
        path = self._make_large_file(30 * 1024 * 1024)
        try:
            mock_run.return_value = MagicMock()
            mp3_path = path + ".tmp.mp3"
            wav_path = path + ".tmp.wav"

            call_count = [0]

            def fake_getsize(p):
                if p == path:
                    return 30 * 1024 * 1024
                if p == wav_path:
                    return 30 * 1024 * 1024  # wav still too large
                if p == mp3_path:
                    return 10 * 1024 * 1024
                return orig_getsize(p) if 'orig_getsize' in dir() else 0

            orig_getsize = os.path.getsize
            mktemp_vals = [wav_path, mp3_path]

            with patch("os.path.getsize", side_effect=fake_getsize):
                with patch("tempfile.mktemp", side_effect=mktemp_vals):
                    with patch("os.unlink"):  # avoid deleting wav
                        result = groq_transcribe._compress_audio(path, max_size_mb=25)
            self.assertEqual(result, mp3_path)
            # Two ffmpeg calls
            self.assertEqual(mock_run.call_count, 2)
            # Second call should contain libmp3lame
            second_args = mock_run.call_args_list[1][0][0]
            self.assertIn("libmp3lame", second_args)
        finally:
            os.unlink(path)


class TestCleanWords(unittest.TestCase):
    def test_valid_words(self):
        words = [{"word": "你好", "start": 0, "end": 0.5}]
        out = groq_transcribe._clean_words(words)
        self.assertEqual(len(out), 1)

    def test_empty_word_skipped(self):
        words = [{"word": "", "start": 0, "end": 0.5}]
        self.assertEqual(len(groq_transcribe._clean_words(words)), 0)

    def test_none_start_skipped(self):
        words = [{"word": "你好", "start": None, "end": 0.5}]
        self.assertEqual(len(groq_transcribe._clean_words(words)), 0)

    def test_inverted_time_skipped(self):
        words = [{"word": "你好", "start": 1.0, "end": 0.5}]
        self.assertEqual(len(groq_transcribe._clean_words(words)), 0)


class TestTranscribeGroqApiErrors(unittest.TestCase):
    def _make_audio(self):
        tf = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tf.write(b"\x00" * 1024)
        tf.close()
        return tf.name

    def _mock_response(self, status_code, json_data=None, text=""):
        m = MagicMock()
        m.status_code = status_code
        m.text = text
        m.json.return_value = json_data or {}
        return m

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post")
    def test_success(self, mock_post, mock_compress):
        mock_post.return_value = self._mock_response(200, {
            "segments": [{"start": 0, "end": 1, "text": "你好"}],
            "words": [{"word": "你好", "start": 0, "end": 1}],
        })
        path = self._make_audio()
        try:
            result = groq_transcribe.transcribe_groq(path, api_key="k")
            self.assertEqual(len(result["segments"]), 1)
            self.assertEqual(result["segments"][0]["text"], "你好")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post")
    def test_401_exits(self, mock_post, mock_compress):
        mock_post.return_value = self._mock_response(401, text="unauthorized")
        path = self._make_audio()
        try:
            with self.assertRaises(SystemExit):
                groq_transcribe.transcribe_groq(path, api_key="bad")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post")
    def test_429_exits(self, mock_post, mock_compress):
        mock_post.return_value = self._mock_response(429, text="rate limit")
        path = self._make_audio()
        try:
            with self.assertRaises(SystemExit):
                groq_transcribe.transcribe_groq(path, api_key="k")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post")
    def test_500_exits(self, mock_post, mock_compress):
        mock_post.return_value = self._mock_response(500, text="server error")
        path = self._make_audio()
        try:
            with self.assertRaises(SystemExit):
                groq_transcribe.transcribe_groq(path, api_key="k")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post", side_effect=groq_transcribe.requests.Timeout)
    def test_timeout_exits(self, mock_post, mock_compress):
        path = self._make_audio()
        try:
            with self.assertRaises(SystemExit):
                groq_transcribe.transcribe_groq(path, api_key="k")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post", side_effect=groq_transcribe.requests.ConnectionError("fail"))
    def test_connection_error_exits(self, mock_post, mock_compress):
        path = self._make_audio()
        try:
            with self.assertRaises(SystemExit):
                groq_transcribe.transcribe_groq(path, api_key="k")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post")
    def test_empty_segments_exits(self, mock_post, mock_compress):
        mock_post.return_value = self._mock_response(200, {"segments": [], "words": []})
        path = self._make_audio()
        try:
            with self.assertRaises(SystemExit):
                groq_transcribe.transcribe_groq(path, api_key="k")
        finally:
            os.unlink(path)

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    @patch("groq_transcribe.requests.post")
    def test_quality_metrics_preserved(self, mock_post, mock_compress):
        mock_post.return_value = self._mock_response(200, {
            "segments": [{"start": 0, "end": 1, "text": "你好",
                          "no_speech_prob": 0.1, "avg_logprob": -0.2}],
            "words": [],
        })
        path = self._make_audio()
        try:
            result = groq_transcribe.transcribe_groq(path, api_key="k")
            self.assertIn("no_speech_prob", result["segments"][0])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
