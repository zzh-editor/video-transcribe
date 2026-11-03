import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bootstrap import requirements_for, _runtime_python


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


if __name__ == "__main__":
    unittest.main()
