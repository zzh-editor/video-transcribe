"""Tests for groq_transcribe.py — API error branches + _compress_audio."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import groq_transcribe


class TestCompressAudioSmallFile(unittest.TestCase):
    def test_16k_mono_ogg_under_limit_returns_original(self):
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
            tf.write(b"\x00" * 1024)
            path = tf.name
        try:
            # already a 16kHz mono Opus/OGG and 1KB < 25MB → zero conversion
            with patch("groq_transcribe._get_audio_info", return_value={
                    "codec": "opus", "rate": "16000", "channels": 1}):
                with patch("groq_transcribe._get_duration") as mock_dur:
                    result = groq_transcribe._compress_audio(path, max_size_mb=25)
            self.assertEqual(result, path)
            mock_dur.assert_not_called()
        finally:
            os.unlink(path)

    def test_mp3_under_limit_still_converts(self):
        # MP3 fits the cap but is NOT 16kHz mono Opus/OGG → must still be
        # normalised once (direct MP3 → OGG), so duration IS probed.
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tf:
            tf.write(b"\x00" * 1024)
            path = tf.name
        try:
            with patch("groq_transcribe._get_audio_info", return_value={
                    "codec": "mp3", "rate": "44100", "channels": 2}):
                with patch("subprocess.run") as mock_run:
                    mock_run.return_value = MagicMock()
                    with patch("groq_transcribe._get_duration", return_value=300.0):
                        with patch("groq_transcribe.os.path.getsize",
                                   side_effect=lambda p: 5 * 1024 * 1024 if p == path
                                   else 2 * 1024 * 1024):
                            with patch("tempfile.mktemp", return_value=path + ".tmp.ogg"):
                                result = groq_transcribe._compress_audio(path, max_size_mb=25)
            self.assertTrue(result.endswith(".ogg"))
            args = mock_run.call_args[0][0]
            self.assertIn("libopus", args)
            self.assertIn("-ar", args)
            self.assertIn("16000", args)
        finally:
            os.unlink(path)
            tmp = path + ".tmp.ogg"
            if os.path.exists(tmp):
                os.unlink(tmp)


class TestCompressAudioLargeFile(unittest.TestCase):
    def _make_large_file(self, size_bytes):
        tf = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        tf.write(b"\x00" * size_bytes)
        tf.close()
        return tf.name

    @patch("groq_transcribe._get_duration", return_value=3600.0)
    @patch("groq_transcribe.subprocess.run")
    def test_converts_to_opus_when_over_limit(self, mock_run, mock_dur):
        path = self._make_large_file(30 * 1024 * 1024)  # 30MB > 25MB
        try:
            mock_run.return_value = MagicMock()
            ogg_path = path + ".tmp.ogg"

            def fake_getsize(p):
                if p == path:
                    return 30 * 1024 * 1024
                # tmp_ogg → pretend 18MB, under the cap
                return 18 * 1024 * 1024

            with patch("os.path.getsize", side_effect=fake_getsize):
                with patch("tempfile.mktemp", return_value=ogg_path):
                    result = groq_transcribe._compress_audio(path, max_size_mb=25)
            self.assertEqual(result, ogg_path)
            args = mock_run.call_args[0][0]
            self.assertIn("libopus", args)
            self.assertIn("-ar", args)
            self.assertIn("16000", args)
            # 1h = 3600s → 25MB*0.9*8/3600 ≈ 52kbps
            self.assertIn("52K", args)
        finally:
            os.unlink(path)
            tmp = path + ".tmp.ogg"
            if os.path.exists(tmp):
                os.unlink(tmp)

    @patch("groq_transcribe._get_duration", return_value=3600.0)
    @patch("groq_transcribe.subprocess.run")
    def test_bitrate_halves_when_still_over(self, mock_run, mock_dur):
        path = self._make_large_file(30 * 1024 * 1024)
        try:
            mock_run.return_value = MagicMock()
            ogg_1 = path + ".tmp1.ogg"
            ogg_2 = path + ".tmp2.ogg"
            seq = iter([ogg_1, ogg_2])
            sizes = iter([30 * 1024 * 1024, 18 * 1024 * 1024])

            def fake_getsize(p):
                if p == path:
                    return 30 * 1024 * 1024
                return next(sizes)

            with patch("os.path.getsize", side_effect=fake_getsize):
                with patch("tempfile.mktemp", side_effect=lambda **kw: next(seq)):
                    with patch("os.unlink"):
                        result = groq_transcribe._compress_audio(path, max_size_mb=25)
            self.assertEqual(result, ogg_2)
            self.assertEqual(mock_run.call_count, 2)
            first = mock_run.call_args_list[0][0][0]
            second = mock_run.call_args_list[1][0][0]
            self.assertIn("52K", first)
            self.assertIn("26K", second)
        finally:
            os.unlink(path)

    @patch("groq_transcribe._get_duration", return_value=28800.0)  # 8h
    @patch("groq_transcribe.subprocess.run")
    def test_gives_up_at_bitrate_floor(self, mock_run, mock_dur):
        path = self._make_large_file(30 * 1024 * 1024)
        try:
            mock_run.return_value = MagicMock()

            def fake_getsize(p):
                if p == path:
                    return 30 * 1024 * 1024
                return 30 * 1024 * 1024  # every re-encode still over

            with patch("os.path.getsize", side_effect=fake_getsize):
                with patch("tempfile.mktemp", side_effect=lambda **kw: path + ".tmp.ogg"):
                    with patch("os.unlink"):
                        with self.assertRaises(SystemExit):
                            groq_transcribe._compress_audio(path, max_size_mb=25)
            # Attempt 1 at the 16k floor, then halving breaks → single encode
            self.assertEqual(mock_run.call_count, 1)
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

    class _FixedResponse:
        def __init__(self, status_code, data=None, text=""):
            self.status_code = status_code
            self._data = data or {}
            self.text = text

        def json(self):
            return self._data

    @patch("groq_transcribe._compress_audio", side_effect=lambda p, m=25: p)
    def test_chunked_encoding_retry_succeeds(self, mock_compress):
        responses = [
            groq_transcribe.requests.exceptions.ChunkedEncodingError("Connection reset by peer"),
            self._FixedResponse(200, {
                "segments": [{"start": 0, "end": 1, "text": "重试成功"}],
                "words": [],
            }),
        ]
        with patch("groq_transcribe.requests.post", side_effect=responses) as mock_post:
            path = self._make_audio()
            try:
                result = groq_transcribe.transcribe_groq(path, api_key="k")
                self.assertEqual(result["segments"][0]["text"], "重试成功")
                self.assertEqual(mock_post.call_count, 2)
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