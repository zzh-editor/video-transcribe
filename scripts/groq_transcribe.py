#!/usr/bin/env python3
"""Transcribe audio using Groq's whisper-large-v3 API with word timestamps."""

import os
import sys
import time
import requests

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_FILE_SIZE_MB = 25


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
            f"error: audio file {file_size_mb:.0f}MB exceeds Groq free tier limit "
            f"({MAX_FILE_SIZE_MB}MB). Use a local model instead.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"engine: groq ({model})", file=sys.stderr)
    print(f"    transcribing: {audio_path}", file=sys.stderr)
    t0 = time.time()

    headers = {"Authorization": f"Bearer {api_key}"}
    data: dict = {
        "model": model,
        "response_format": "verbose_json",
    }
    if language:
        data["language"] = language

    with open(audio_path, "rb") as f:
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
        words_raw = seg.get("words", [])
        words = []
        for w in words_raw:
            word_text = w.get("word", "").strip()
            ws = w.get("start")
            we = w.get("end")
            if word_text and ws is not None and we is not None and we > ws:
                words.append({
                    "word": word_text,
                    "start": float(ws),
                    "end": float(we),
                })
        segments.append({
            "start": seg.get("start", 0),
            "end": seg.get("end", 0),
            "text": seg.get("text", "").strip(),
            "words": words,
        })

    segments.sort(key=lambda s: s["start"])
    return segments
