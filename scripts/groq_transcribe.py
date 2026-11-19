#!/usr/bin/env python3
"""Transcribe audio using Groq's whisper-large-v3 API.

Groq-supported files under the 25MB upload cap are sent unchanged to avoid
unnecessary lossy re-encoding. Oversized or unsupported files are converted to
16kHz mono Opus/OGG and sized to fit the cap.
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
SUPPORTED_AUDIO_SUFFIXES = {
    ".flac", ".m4a", ".mp3", ".mp4", ".mpeg", ".mpga", ".ogg", ".wav", ".webm",
}

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
# 429 gets extra attempts on top: chunked long-audio runs graze the free-tier
# 20 RPM cap and one burst shouldn't kill a 48-chunk run.
_429_EXTRA_ATTEMPTS = 4


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


def _get_audio_info(path: str) -> dict | None:
    """ffprobe the first audio stream: codec, sample rate, channels."""
    cmd = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", path]
    try:
        data = json.loads(subprocess.check_output(cmd))
        for stream in data.get("streams", []):
            if stream.get("codec_type") != "audio":
                continue
            return {
                "codec": stream.get("codec_name"),
                "rate": stream.get("sample_rate"),
                "channels": stream.get("channels"),
            }
    except Exception:
        pass
    return None


def _can_upload_as_is(path: str, max_bytes: int) -> bool:
    return (os.path.getsize(path) <= max_bytes
            and os.path.splitext(path)[1].lower() in SUPPORTED_AUDIO_SUFFIXES)


def _compress_audio(audio_path: str, max_size_mb: int = 25) -> str:
    """Keep supported files under the cap unchanged; otherwise convert to Opus."""
    max_bytes = max_size_mb * 1024 * 1024
    file_size = os.path.getsize(audio_path)

    if _can_upload_as_is(audio_path, max_bytes):
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


# ── VAD chunking for long audio ─────────────────────────────────────
# Groq's own docs recommend splitting large audio into smaller segments
# processed independently. Beyond the 25MB upload cap this also fixes two
# decoding failures observed on a 68-minute single call: language drift
# (Chinese speech transcribed as English mid-file) and decoder collapse
# after long silences ("."-only segments, lost real speech). Chunks are cut
# at VAD silence gaps so no word is split, each call re-anchors its own
# decoding context, and per-call silence is near zero.
# Groq free tier whisper-large-v3 limits: 20 RPM / 2000 RPD / 7200 audio-sec
# per hour (console.groq.com/docs/rate-limits). A fixed inter-call pause of
# 60/20 + margin keeps the request rate safely under RPM regardless of how
# fast small chunks return; audio-seconds are a non-issue since VAD chunks
# total the speech duration (~9 min for a 68-min lecture), not the file length.
CHUNK_MIN_AUDIO_S = 1800.0   # audio longer than 30 min → chunked calls
CHUNK_MERGE_GAP_S = 10.0      # speech regions closer than this form one chunk
CHUNK_MAX_S = 600.0           # split chunks longer than 10 min
CHUNK_PAD_S = 0.2             # pad around speech so onsets keep breath
CHUNK_THROTTLE_S = 3.3        # ≥ 60s/20RPM + margin → hard-capped under RPM


def plan_chunks(vad_map: dict, merge_gap: float = CHUNK_MERGE_GAP_S,
                max_len: float = CHUNK_MAX_S) -> list[tuple[float, float]]:
    """Group VAD speech regions into greedy upload chunks.

    A region joins the current chunk while the inter-region gap is small
    (prosody, not a scene break) and the chunk stays under max_len;
    otherwise a new chunk starts. Cutting at silence gaps means no chunk
    boundary ever falls inside speech.
    """
    regions = [(r["start"], r["end"]) for r in vad_map["speech"]]
    chunks: list[tuple[float, float]] = []
    cur_s, cur_e = None, None
    for s, e in regions:
        if cur_s is None:
            cur_s, cur_e = s, e
        elif s - cur_e < merge_gap and (e - cur_s) <= max_len:
            cur_e = max(cur_e, e)
        else:
            chunks.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    if cur_s is not None:
        chunks.append((cur_s, cur_e))
    return chunks


def _cut_chunk(audio_path: str, start: float, end: float, out_path: str) -> None:
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-ss", f"{max(0.0, start - CHUNK_PAD_S):.3f}",
         "-to", f"{end + CHUNK_PAD_S:.3f}",
         "-i", audio_path, "-vn", "-ar", "16000", "-ac", "1",
         "-c:a", "libopus", "-b:a", "48K", "-vbr", "on", out_path],
        check=True,
    )


def _transcribe_chunked(
    audio_path: str,
    api_key: str,
    language: str | None,
    model: str,
    prompt: str | None,
    vad_map: dict,
) -> dict:
    chunks = plan_chunks(vad_map)
    total_speech = sum(e - s for s, e in chunks)
    print(f"groq: chunked mode — {len(chunks)} VAD chunks, "
          f"{total_speech/60:.1f} min speech of {vad_map['duration']/60:.0f} min audio",
          file=sys.stderr)
    import tempfile

    all_segments: list[dict] = []
    all_words: list[dict] = []
    detected: str | None = None
    t0 = time.time()
    for i, (start, end) in enumerate(chunks, 1):
        fd, tmp = tempfile.mkstemp(suffix=".ogg", prefix="groqchunk_")
        os.close(fd)
        try:
            _cut_chunk(audio_path, start, end, tmp)
            chunk_lang = language or detected
            result = _transcribe_request(tmp, api_key, model, chunk_lang, prompt)
            if detected is None and result.get("language"):
                detected = result["language"]
                if language is None:
                    print(f"groq: first chunk detected language {detected}; "
                          "locking remaining chunks to it", file=sys.stderr)
            offset = max(0.0, start - CHUNK_PAD_S)
            for seg in result["segments"]:
                seg["start"] += offset
                seg["end"] += offset
                all_segments.append(seg)
            for w in result["words"]:
                w["start"] += offset
                w["end"] += offset
                all_words.append(w)
            print(f"  chunk {i}/{len(chunks)} [{start:.0f}-{end:.0f}s] "
                  f"→ {len(result['segments'])} segs", file=sys.stderr)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        if i < len(chunks):
            time.sleep(CHUNK_THROTTLE_S)

    print(f"transcription took {time.time() - t0:.1f}s "
          f"({len(all_segments)} raw segments)", file=sys.stderr)
    all_segments.sort(key=lambda s: s["start"])
    all_words.sort(key=lambda w: w["start"])
    return {
        "segments": all_segments,
        "words": all_words,
        "language": language or detected,
    }


def _transcribe_request(
    upload_path: str,
    api_key: str,
    model: str,
    language: str | None,
    prompt: str | None,
) -> dict:
    """POST one audio file to Groq and return parsed transcript data.

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

    retryable = frozenset({500, 502, 503, 504, 524})
    for attempt in range(RETRY_ATTEMPTS + _429_EXTRA_ATTEMPTS):
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

        if resp.status_code == 429:
            # Free tier is 20 RPM and chunked runs graze the cap. Groq sends
            # retry-after on 429 — honour it, with extra attempts so a long
            # chunked run survives a burst instead of dying at 2 retries.
            if attempt < RETRY_ATTEMPTS + _429_EXTRA_ATTEMPTS - 1:
                wait = float(resp.headers.get("retry-after") or RETRY_BASE_S * (attempt + 1))
                print(f"warning: Groq rate limit (429), retrying in {wait:.0f}s "
                      f"({attempt + 2}/{RETRY_ATTEMPTS + _429_EXTRA_ATTEMPTS})",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            print("error: Groq rate limit exceeded (429) repeatedly. "
                  "Free tier is 20 RPM; wait a minute and retry.", file=sys.stderr)
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
        return {
            "segments": segments,
            "words": words,
            "language": result.get("language"),
        }
    sys.exit(1)  # unreachable, keeps flow analyzer happy


def _transcribe_single(
    audio_path: str,
    api_key: str,
    language: str | None,
    model: str,
    prompt: str | None,
) -> dict:
    """Single-request path: normalise once, one upload, one transcription."""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    if file_size_mb > MAX_FILE_SIZE_MB:
        print(
            f"audio: {file_size_mb:.0f}MB exceeds Groq limit ({MAX_FILE_SIZE_MB}MB), "
            f"converting via ffmpeg …",
            file=sys.stderr,
        )
    elif not _can_upload_as_is(audio_path, MAX_FILE_SIZE_MB * 1024 * 1024):
        print(
            f"audio: converting {audio_path} to 16kHz mono Opus/OGG for Groq upload …",
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
    if not words:
        print("warning: Groq API returned no word-level timestamps; "
              "segment boundaries will use raw Groq windows (~5s). "
              "This degrades subtitle timestamp precision. "
              "Consider using mlx-whisper or faster-whisper for word-level "
              "timestamps, or check if Groq model supports word timestamps.",
              file=sys.stderr)
    segments.sort(key=lambda s: s["start"])
    words.sort(key=lambda w: w["start"])
    return {
        "segments": segments,
        "words": words,
        "language": result.get("language"),
    }


def transcribe_groq(
    audio_path: str,
    api_key: str,
    language: str | None = None,
    model: str = "whisper-large-v3",
    prompt: str | None = None,
    vad_map: dict | None = None,
    duration: float | None = None,
) -> dict:
    audio_path = str(audio_path)
    # Long audio: one 68-min call showed language drift (Chinese decoded as
    # English mid-file) and decoder collapse after silences. Groq's docs
    # recommend chunking; VAD gaps give word-safe cut points.
    if vad_map and duration and duration > CHUNK_MIN_AUDIO_S:
        return _transcribe_chunked(
            audio_path, api_key, language, model, prompt, vad_map)
    return _transcribe_single(audio_path, api_key, language, model, prompt)
