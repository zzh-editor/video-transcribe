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

# ── 通用依赖 ──────────────────────────────────────────────────
venv/bin/pip install --quiet socksio requests jieba==0.42.1

# ── Whisper 引擎（按平台分支）───────────────────────────────────
if [[ "$(uname)" == "Darwin" && "$(uname -m)" == "arm64" ]]; then
    echo "[setup] macOS arm64 — installing mlx-whisper + VAD..."
    venv/bin/pip install --quiet mlx-whisper soundfile silero-vad-notorch onnxruntime
    # faster-whisper 可选，未强制，避免额外失败面
    venv/bin/pip install --quiet faster-whisper || echo "[setup] warning: faster-whisper optional install failed" >&2
else
    echo "[setup] non-macOS — installing faster-whisper..."
    venv/bin/pip install --quiet faster-whisper
    # silero VAD 在非 macOS 不强制，faster-whisper 自带 vad_filter
fi

if [ ! -d models ]; then
    echo "[setup] creating models directory..."
    mkdir -p models
fi

echo "[setup] done. venv/ and models/ are ready."
