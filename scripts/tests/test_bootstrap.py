import unittest
from pathlib import Path
import json
import os
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bootstrap import requirements_for, _resolve_engine, _runtime_python


class TestBootstrap(unittest.TestCase):
    def test_groq_requirements_include_alignment_and_tokenization(self):
        self.assertEqual(
            requirements_for("groq", "Linux", "x86_64"),
            [("jieba", "jieba==0.42.1"), ("numpy", "numpy"), ("requests", "requests")],
        )

    def test_local_backend_is_platform_specific(self):
        mac = requirements_for("local", "Darwin", "arm64")
        windows = requirements_for("local", "Windows", "AMD64")
        self.assertIn(("mlx_whisper", "mlx-whisper"), mac)
        self.assertIn(("faster_whisper", "faster-whisper"), windows)

    def test_venv_python_path_is_platform_specific(self):
        env = Path("env")
        self.assertEqual(_runtime_python(env, windows=True), env / "Scripts/python.exe")
        self.assertEqual(_runtime_python(env, windows=False), env / "bin/python3")

    def test_auto_engine_reads_only_explicit_config_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"engine": "local"}), encoding="utf-8")
            with patch.dict(os.environ, {"VIDEO_TRANSCRIBE_CONFIG": str(config)}):
                self.assertEqual(_resolve_engine("auto"), "local")

    def test_auto_engine_requires_config_or_explicit_selection(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "--engine"):
                _resolve_engine("auto")

    def test_invalid_explicit_config_fails_instead_of_falling_back(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text("{", encoding="utf-8")
            with patch.dict(os.environ, {"VIDEO_TRANSCRIBE_CONFIG": str(config)}):
                with self.assertRaisesRegex(RuntimeError, "VIDEO_TRANSCRIBE_CONFIG"):
                    _resolve_engine("auto")


if __name__ == "__main__":
    unittest.main()
