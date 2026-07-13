#!/usr/bin/env python3
"""Transcribe audio using Groq's whisper-large-v3 API."""

import os
import sys
import time
import tempfile
import subprocess
import json
import requests

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_FILE_SIZE_MB = 25


def _get_duration(path: str) -> float:
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path]
    try:
        data = json.loads(subprocess.check_output(cmd))
        for stream in data.get("streams", []):
            dur = stream.get("duration")
            if dur:
                return float(dur)
    except Exception:
        pass
    return 0.0


def _compress_audio(audio_path: str, max_size_mb: int = 25) -> str:
    """Compress/convert audio to fit within max_size_mb for Groq API upload.

    Strategy: 16kHz mono WAV first (Whisper standard format). If still over
    limit, encode to MP3 at a bitrate calculated to fit under the limit.
    Returns path to compressed file (caller must clean up).
    """
    max_bytes = max_size_mb * 1024 * 1024
    file_size = os.path.getsize(audio_path)

    if file_size <= max_bytes:
        return audio_path

    duration = _get_duration(audio_path)
    if duration <= 0:
        print("error: could not determine audio duration via ffprobe", file=sys.stderr)
        sys.exit(1)

    # Step 1: 16 kHz mono WAV (uncompressed, but downsampling shrinks it)
    tmp_wav = tempfile.mktemp(suffix=".wav")
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1",
             "-c:a", "pcm_s16le", tmp_wav],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"error: ffmpeg conversion failed: {e.stderr.decode()[:200]}",
              file=sys.stderr)
        sys.exit(1)

    wav_size = os.path.getsize(tmp_wav)
    if wav_size <= max_bytes:
        print(f"audio: converted to 16kHz mono WAV "
              f"({file_size // 1024 ** 2}MB → {wav_size // 1024 ** 2}MB)",
              file=sys.stderr)
        return tmp_wav

    # Step 2: Still too large — MP3 at calculated bitrate
    # 90 % safety margin for container / header overhead
    target_bits = max_bytes * 8 * 0.9
    bitrate = int(target_bits / duration)
    bitrate = (bitrate // 8000) * 8000            # round down to 8k step
    bitrate = max(bitrate, 16000)                  # floor 16 kbps
    bitrate = min(bitrate, 128000)                 # ceiling 128 kbps

    tmp_mp3 = tempfile.mktemp(suffix=".mp3")
    os.unlink(tmp_wav)  # discard intermediate WAV

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", audio_path, "-ar", "16000", "-ac", "1",
             "-b:a", str(bitrate), "-c:a", "libmp3lame", tmp_mp3],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        print(f"error: ffmpeg compression failed: {e.stderr.decode()[:200]}",
              file=sys.stderr)
        sys.exit(1)

    mp3_size = os.path.getsize(tmp_mp3)
    print(f"audio: compressed to 16kHz mono MP3 @ {bitrate // 1000}kbps "
          f"({file_size // 1024 ** 2}MB → {mp3_size // 1024 ** 2}MB)",
          file=sys.stderr)
    return tmp_mp3


def transcribe_groq(
    audio_path: str,
    api_key: str,
    language: str | None = None,
    model: str = "whisper-large-v3",
) -> list[dict]:
    audio_path = str(audio_path)
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        print(
            f"audio: {file_size_mb:.0f}MB exceeds Groq limit ({MAX_FILE_SIZE_MB}MB), "
            f"compressing via ffmpeg …",
            file=sys.stderr,
        )
    upload_path = _compress_audio(audio_path, MAX_FILE_SIZE_MB)

    print(f"engine: groq ({model})", file=sys.stderr)
    print(f"    transcribing: {upload_path}", file=sys.stderr)
    t0 = time.time()

    headers = {"Authorization": f"Bearer {api_key}"}
    data = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
    ]
    if language:
        data.append(("language", language))

    try:
        with open(upload_path, "rb") as f:
            files = {"file": f}
            try:
                resp = requests.post(
                    GROQ_STT_URL,
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=600,
                )
            except requests.Timeout:
                print("error: Groq API request timed out (600s)", file=sys.stderr)
                sys.exit(1)
            except requests.ConnectionError as e:
                print(f"error: Groq API connection failed: {e}", file=sys.stderr)
                sys.exit(1)

        elapsed = time.time() - t0
        print(f"transcription took {elapsed:.1f}s", file=sys.stderr)

        if resp.status_code == 401:
            print("error: Groq API key is invalid (401). Check your API key.", file=sys.stderr)
            sys.exit(1)
        elif resp.status_code == 429:
            print("error: Groq rate limit exceeded (429). Try again later.", file=sys.stderr)
            sys.exit(1)
        elif resp.status_code != 200:
            print(f"error: Groq API returned {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
            sys.exit(1)

        result = resp.json()
        raw_segments = result.get("segments", [])
        if not raw_segments:
            print("error: no segments in Groq API response", file=sys.stderr)
            sys.exit(1)

        segments = []
        for seg in raw_segments:
            segments.append({
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "text": seg.get("text", "").strip(),
            })

        segments.sort(key=lambda s: s["start"])
        return segments
    finally:
        if upload_path != audio_path and os.path.exists(upload_path):
            os.unlink(upload_path)
