#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$SCRIPT_DIR"

echo "[setup] video-transcribe environment check"

if [ ! -d venv ]; then
    echo "[setup] creating Python virtual environment..."
    python3 -m venv venv
fi

echo "[setup] installing Python dependencies..."

# ── Whisper引擎 ──────────────────────────────────────────────────
venv/bin/pip install --quiet mlx-whisper faster-whisper socksio soundfile

# ── Groq API ────────────────────────────────────────────────────
venv/bin/pip install --quiet requests

# ── jieba 中文分词（Groq 中文 word timestamp 适配）───────────
venv/bin/pip install --quiet jieba==0.42.1

# ── VAD (silero-vad-notorch + onnxruntime, no torch) ─────────────
venv/bin/pip install --quiet silero-vad-notorch onnxruntime

if [ ! -d models ]; then
    echo "[setup] creating models directory..."
    mkdir -p models
fi

echo "[setup] done. venv/ and models/ are ready."
