#!/usr/bin/env python3
"""Transcribe audio using Groq's whisper-large-v3 API.

Files up to 25MB go straight through. Larger inputs are converted to a heavily
compressed Opus (OGG) file sized to land under the 25MB upload cap, then sent
as a single request — one upload per file no matter how long the audio is.

Opus is the best space/quality tradeoff for speech among Groq's supported
formats (FLAC/MP3/M4A/MPEG/MPGA/OGG/WAV/WEBM); 16kHz mono speech stays clear
around 24-32kbps, roughly 3-4x denser than MP3 at equal quality. FLAC is
lossless but barely helps and MP3 is densest only at audible quality loss, so
Opus/OGG is fixed as the single compression route for every oversized input.
"""

import os
import sys
import time
import tempfile
import subprocess
import json
import requests

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
MAX_FILE_SIZE_MB = 25

# Single-request compression settings (fixed codec, applied to every oversized
# input — no format probing).
COMPRESS_SUFFIX = ".ogg"
COMPRESS_BITRATE_FLOOR_K = 16   # below this speech quality degrades fast
COMPRESS_BITRATE_CEIL_K = 128
COMPRESS_SAFETY = 0.9          # aim for 90% of the cap (container overhead)
COMPRESS_MAX_ATTEMPTS = 3      # if still over after re-encode, halve bitrate

# Retry policy for transient server-side failures (429 / 5xx). Timeouts and
# connection errors still fail fast.
RETRY_ATTEMPTS = 3         # initial request + 2 retries
RETRY_BASE_S = 1.0         # backoff: 1s, 2s


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
    """Compress an oversized file into a single Opus (OGG) upload ≤ max_size_mb.

    The bitrate is derived from the file duration so the result lands just under
    the cap; if the encoder overshoots, the bitrate is halved and re-encoded.
    Returns path to the compressed file (caller must clean up).
    """
    max_bytes = max_size_mb * 1024 * 1024
    file_size = os.path.getsize(audio_path)

    if file_size <= max_bytes:
        return audio_path

    duration = _get_duration(audio_path)
    if duration <= 0:
        print("error: could not determine audio duration via ffprobe", file=sys.stderr)
        sys.exit(1)

    target_bits = max_bytes * COMPRESS_SAFETY * 8
    bitrate = (int(target_bits / duration) // 1000) * 1000
    bitrate = max(bitrate, COMPRESS_BITRATE_FLOOR_K * 1000)
    bitrate = min(bitrate, COMPRESS_BITRATE_CEIL_K * 1000)
    kbps = bitrate // 1000

    tmp_ogg = tempfile.mktemp(suffix=COMPRESS_SUFFIX)
    for attempt in range(COMPRESS_MAX_ATTEMPTS):
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", audio_path, "-vn",
                 "-ar", "16000", "-ac", "1",
                 "-c:a", "libopus", "-b:a", f"{kbps}K", "-vbr", "on",
                 tmp_ogg],
                capture_output=True, check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"error: ffmpeg compression failed: {e.stderr.decode()[:200]}",
                  file=sys.stderr)
            sys.exit(1)

        ogg_size = os.path.getsize(tmp_ogg)
        if ogg_size <= max_bytes:
            print(f"audio: compressed to Opus/OGG @ {kbps}kbps "
                  f"({file_size // 1024 ** 2}MB → {ogg_size // 1024 ** 2}MB)",
                  file=sys.stderr)
            return tmp_ogg

        # Still over the cap: halve the bitrate and re-encode from the source.
        os.unlink(tmp_ogg)
        kbps //= 2
        if kbps < COMPRESS_BITRATE_FLOOR_K:
            break
        tmp_ogg = tempfile.mktemp(suffix=COMPRESS_SUFFIX)

    print("error: could not compress audio under Groq's 25MB cap; "
          "the file is too long for the bitrate floor", file=sys.stderr)
    sys.exit(1)


def _clean_words(words: list) -> list[dict]:
    cleaned = []
    for w in words:
        text = (w.get("word") or "").strip()
        start = w.get("start")
        end = w.get("end")
        if not text or start is None or end is None:
            continue
        try:
            s, e = float(start), float(end)
        except (ValueError, TypeError):
            continue
        if e <= s:
            continue
        cleaned.append({"word": text, "start": s, "end": e})
    return cleaned


def _transcribe_request(
    upload_path: str,
    api_key: str,
    model: str,
    language: str | None,
    prompt: str | None,
) -> dict:
    """POST one audio file to Groq and return parsed {"segments","words"}.

    Transient failures (429 / 5xx / 524) are retried with backoff; auth
    failures and timeouts exit immediately. A 200 with no segments is not
    fatal here — the caller decides whether the whole file is silent.
    """
    headers = {"Authorization": f"Bearer {api_key}"}
    data = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "segment"),
        ("timestamp_granularities[]", "word"),
        ("temperature", "0"),
    ]
    if language:
        data.append(("language", language))
    if prompt:
        data.append(("prompt", prompt))

    retryable = frozenset({429, 500, 502, 503, 504, 524})
    for attempt in range(RETRY_ATTEMPTS):
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
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_BASE_S * (attempt + 1))
                    continue
                print("error: Groq API request timed out (600s)", file=sys.stderr)
                sys.exit(1)
            except requests.ConnectionError as e:
                print(f"error: Groq API connection failed: {e}", file=sys.stderr)
                sys.exit(1)
            except requests.exceptions.ChunkedEncodingError as e:
                # Response stream reset mid-read (common with Cloudflare
                # 5xx/524 timeouts) — transient, retry.
                if attempt < RETRY_ATTEMPTS - 1:
                    time.sleep(RETRY_BASE_S * (attempt + 1))
                    continue
                print(f"error: Groq API response interrupted: {e}", file=sys.stderr)
                sys.exit(1)

        if resp.status_code in retryable and attempt < RETRY_ATTEMPTS - 1:
            wait = RETRY_BASE_S * (attempt + 1)
            print(f"warning: Groq API returned {resp.status_code}, "
                  f"retrying in {wait:.0f}s ({attempt + 2}/{RETRY_ATTEMPTS})",
                  file=sys.stderr)
            time.sleep(wait)
            continue

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
        segments = []
        for seg in result.get("segments", []):
            entry = {
                "start": seg.get("start", 0),
                "end": seg.get("end", 0),
                "text": seg.get("text", "").strip(),
            }
            for quality_key in ("no_speech_prob", "avg_logprob",
                                "compression_ratio"):
                if quality_key in seg and seg[quality_key] is not None:
                    entry[quality_key] = seg[quality_key]
            segments.append(entry)

        words = _clean_words(result.get("words", []))
        return {"segments": segments, "words": words}
    sys.exit(1)  # unreachable, keeps flow analyzer happy


def _transcribe_single(
    audio_path: str,
    api_key: str,
    language: str | None,
    model: str,
    prompt: str | None,
) -> dict:
    """Single-request path: compress once, one upload, one transcription."""
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
    try:
        result = _transcribe_request(upload_path, api_key, model, language, prompt)
    finally:
        if upload_path != audio_path and os.path.exists(upload_path):
            os.unlink(upload_path)

    print(f"transcription took {time.time() - t0:.1f}s", file=sys.stderr)

    segments, words = result["segments"], result["words"]
    if not segments:
        print("error: no segments in Groq API response", file=sys.stderr)
        sys.exit(1)
    segments.sort(key=lambda s: s["start"])
    words.sort(key=lambda w: w["start"])
    return {"segments": segments, "words": words}


def transcribe_groq(
    audio_path: str,
    api_key: str,
    language: str | None = None,
    model: str = "whisper-large-v3",
    prompt: str | None = None,
) -> dict:
    audio_path = str(audio_path)
    return _transcribe_single(audio_path, api_key, language, model, prompt)