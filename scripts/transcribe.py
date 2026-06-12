#!/usr/bin/env python3
"""
Transcribe audio to SRT using Whisper with word-level timestamp segmentation.

Segmentation priority (3-layer + post-process):

  Layer 1 — Strong boundaries (immediate split):
    · Sentence-ending punctuation (。？！.?!…)
    · Protected single-word responses with significant surrounding pause
      (好/对/OK/yes/no 等 → keep as independent subtitle)
    · Natural pause ≥ threshold (300ms zh/ja/ko, 500ms others)

  Layer 2 — Soft splitting for over-length blocks (>max_line_ms or >max_chars):
    a. Last comma/conjunction within first max_chars
    b. Largest word-gap in latter 2/3 of the block
    c. Fallback: even character split

  Layer 3 — Smart merging of short fragments:
    · Merge short (<0.4s, <3 words) into previous block
    · Skip merge if fragment is a protected response word

  Post-process:
    · Drop invalid timestamps (end <= start)
    · Deduplicate consecutive identical text (Whisper repeat artifact)
    · Fix time overlaps between adjacent blocks

Usage:
    python3 scripts/transcribe.py <audio.wav> [options]
"""

import argparse
import os
import platform
import sys
import time
from pathlib import Path


# ── Constants ──────────────────────────────────────────────────────

SENTENCE_END = frozenset(".?!。？！…")
SOFT_BREAK = frozenset(",;:，；：、—")
CONJUNCTIONS = [
    "然后", "所以", "但是", "不过", "而且", "另外", "还有", "接下来",
    "but", "and", "so", "however", "then", "because", "also",
]

# Single-word/character responses that should be kept as independent
# subtitle blocks when isolated by significant pauses.
PROTECTED_WORDS = frozenset({
    # Chinese single-char
    "好", "对", "嗯", "嗯", "是", "不", "行", "哦", "啊", "呀", "喏",
    "嗯哼",
    # Chinese multi-char
    "好的", "对的", "明白", "知道", "可以", "没错", "是的", "不行",
    "好吧", "对了", "对哦", "对啊", "嗯嗯", "好啦", "行了", "可以啊",
    "没问题", "没事", "知道了", "明白了", "没关系",
    # English
    "ok", "okay", "yes", "no", "right", "sure", "yeah", "yep",
    "nope", "nah", "alright", "indeed",
})


# ── Helpers ────────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_pause_threshold(language: str | None) -> float:
    if language and language.startswith(("zh", "ja", "ko")):
        return 0.3
    return 0.5


def _strip_punct(text: str) -> str:
    return text.strip().lower().rstrip(",.?!。？！，、；:\"'").strip()


def is_protected(text: str) -> bool:
    return _strip_punct(text) in PROTECTED_WORDS


def should_isolate(word_text: str, gap_before: float, gap_after: float,
                   pause_threshold: float) -> bool:
    """True if a protected word should stand alone as its own subtitle block."""
    base = _strip_punct(word_text)
    if base not in PROTECTED_WORDS:
        return False
    return gap_before >= pause_threshold or gap_after >= pause_threshold


def get_model_path() -> str:
    skill_dir = Path(__file__).resolve().parent.parent
    models_dir = skill_dir / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    return str(models_dir)


# ── Word data ──────────────────────────────────────────────────────

class Word:
    __slots__ = ("start", "end", "word", "probability")
    def __init__(self, start: float, end: float, word: str, probability: float = 1.0):
        self.start = start
        self.end = end
        self.word = word
        self.probability = probability


# ── Word extraction ────────────────────────────────────────────────

def extract_words(segments: list) -> list[Word]:
    flat: list[Word] = []
    for seg in segments:
        if isinstance(seg, dict):
            words = seg.get("words", [])
        else:
            words = getattr(seg, "words", None) or []
        if not words:
            continue
        for w in words:
            if isinstance(w, dict):
                flat.append(Word(
                    w.get("start", 0.0),
                    w.get("end", 0.0),
                    w.get("word", ""),
                    w.get("probability", 1.0),
                ))
            else:
                flat.append(Word(
                    getattr(w, "start", 0.0),
                    getattr(w, "end", 0.0),
                    getattr(w, "word", ""),
                    getattr(w, "probability", 1.0),
                ))
    return flat


# ── Segmentation (Layer 1 + Layer 2) ──────────────────────────────

def find_soft_cut(words: list[Word], max_chars: int) -> int | None:
    """Find last soft-break position (comma/conjunction) within max_chars."""
    best = None
    for i, w in enumerate(words):
        if sum(len(x.word) for x in words[:i + 1]) > max_chars:
            break
        t = w.word.strip()
        if t and t[-1] in SOFT_BREAK:
            best = i
    if best is not None:
        return best
    # Try conjunctions
    for conj in CONJUNCTIONS:
        for i, w in enumerate(words):
            if w.word.strip().startswith(conj) and i > 0:
                return i - 1
    return None


def find_pause_cut(words: list[Word]) -> int | None:
    """Largest gap in latter 2/3 of the block."""
    best_gap, best_j = 0.2, None
    start = max(1, len(words) // 3)
    for j in range(start, len(words) - 1):
        gap = words[j + 1].start - words[j].end
        if gap > best_gap:
            best_gap, best_j = gap, j
    return best_j


def merge_words_to_segments(words: list[Word], *,
                            max_line_ms: int = 6000,
                            pause_threshold: float = 0.3,
                            max_chars: int = 40) -> list[dict]:
    if not words:
        return []

    n = len(words)
    result: list[dict] = []
    cur: list[Word] = []

    for i, w in enumerate(words):
        wtext = w.word.strip()
        if not wtext:
            cur.append(w)
            continue

        gap_after = words[i + 1].start - w.end if i + 1 < n else 999.0
        gap_before = w.start - words[i - 1].end if i > 0 else 0.0

        # Layer 1a: Protected word at start of new block → isolate immediately
        if not cur and should_isolate(wtext, gap_before, gap_after, pause_threshold):
            result.append({"start": w.start, "end": w.end, "text": wtext})
            continue

        cur.append(w)

        # Build current accumulated text, stripping empty-word artifacts
        cur_text = "".join(x.word for x in cur).replace(" ", "").strip()
        cur_text = cur_text.replace("\u200b", "")
        cur_start = cur[0].start
        cur_end = cur[-1].end
        cur_dur = cur_end - cur_start
        cur_char_len = max(len(cur_text.replace(" ", "")), len("".join(x.word.strip() for x in cur if x.word.strip())))

        # Layer 1b: Sentence-ending punctuation → split
        if wtext[-1] in SENTENCE_END:
            result.append({"start": cur_start, "end": cur_end, "text": cur_text})
            cur = []
            continue

        # Layer 1c: Protected word as only accumulated item with pause after it
        if len(cur) == 1 and gap_after >= pause_threshold and is_protected(wtext):
            result.append({"start": cur_start, "end": cur_end, "text": cur_text})
            cur = []
            continue

        # Layer 1d: Big pause after ≥2 words → split
        if gap_after >= pause_threshold and len(cur) >= 2:
            result.append({"start": cur_start, "end": cur_end, "text": cur_text})
            cur = []
            continue

        # Layer 2: Over-length → soft cut
        if cur_dur >= max_line_ms / 1000 or cur_char_len >= max_chars:
            cut = find_soft_cut(cur, max_chars)
            if cut is not None and cut < len(cur) - 1:
                head = cur[:cut + 1]
                cur = cur[cut + 1:]
                ht = "".join(x.word for x in head).replace("\u200b", "").strip()
                result.append({"start": head[0].start, "end": head[-1].end, "text": ht})
                continue

            cut = find_pause_cut(cur)
            if cut is not None and cut < len(cur) - 1:
                head = cur[:cut + 1]
                cur = cur[cut + 1:]
                ht = "".join(x.word for x in head).replace("\u200b", "").strip()
                result.append({"start": head[0].start, "end": head[-1].end, "text": ht})
                continue

            # Fallback: even split at approximate midpoint
            mid = len(cur) // 2
            head = cur[:mid]
            cur = cur[mid:]
            ht = "".join(x.word for x in head).replace("\u200b", "").strip()
            result.append({"start": head[0].start, "end": head[-1].end, "text": ht})

    if cur:
        ct = "".join(x.word for x in cur).replace("\u200b", "").strip()
        if ct:
            result.append({"start": cur[0].start, "end": cur[-1].end, "text": ct})

    return result


# ── Post-processing (Layer 3) ─────────────────────────────────────

def postprocess(segments: list[dict]) -> list[dict]:
    if not segments:
        return []

    # Phase 1: Drop invalid + deduplicate consecutive identical text
    cleaned: list[dict] = []
    for seg in segments:
        if seg["end"] <= seg["start"]:
            continue
        text = seg["text"]
        if cleaned and text == cleaned[-1]["text"]:
            cleaned[-1]["end"] = max(cleaned[-1]["end"], seg["end"])
            continue
        cleaned.append(seg)

    # Phase 2: Smart merge short fragments (not protected response words)
    merged: list[dict] = []
    for seg in cleaned:
        duration = seg["end"] - seg["start"]
        word_count = seg["text"].count(" ") + 1
        is_short = duration < 0.4 and word_count < 3
        if merged and is_short and not is_protected(seg["text"]):
            merged[-1]["end"] = seg["end"]
            sep = "" if seg["text"][:1] in "，、。？！" else ""
            merged[-1]["text"] = merged[-1]["text"].rstrip("，、；：") + sep + seg["text"]
        else:
            merged.append(seg)

    # Phase 3: Fix time overlaps
    for k in range(1, len(merged)):
        prev_end = merged[k - 1]["end"]
        if merged[k]["start"] < prev_end:
            gap = merged[k]["end"] - prev_end
            if gap > 0.01:
                merged[k]["start"] = prev_end
            else:
                merged[k] = dict(merged[k], start=prev_end + 0.01,
                                 end=prev_end + 0.3)

    return merged


# ── Transcribe backends ────────────────────────────────────────────

def transcribe_mlx(audio_path: str, model_name: str, language: str | None,
                   max_line_length: int, pause_threshold: float,
                   max_line_ms: int) -> list[dict]:
    import mlx_whisper

    models_dir = get_model_path()
    os.environ.setdefault("HF_HOME", models_dir)
    os.environ.setdefault("XDG_CACHE_HOME", str(Path(models_dir) / "cache"))

    print(f"engine: mlx-whisper", file=sys.stderr)
    print(f"model: {model_name}", file=sys.stderr)
    print(f"device: Apple Silicon (MLX)", file=sys.stderr)
    print(f"transcribing: {audio_path}", file=sys.stderr)

    t0 = time.time()
    result = mlx_whisper.transcribe(
        audio_path,
        path_or_hf_repo=model_name,
        language=language,
        word_timestamps=True,
    )
    elapsed = time.time() - t0
    print(f"transcription took {elapsed:.1f}s", file=sys.stderr)

    segments = result.get("segments", [])
    if not segments:
        print("error: no segments in mlx-whisper output", file=sys.stderr)
        sys.exit(1)

    raw_count = len(segments)
    words = extract_words(segments)
    out = merge_words_to_segments(
        words,
        max_line_ms=max_line_ms,
        pause_threshold=pause_threshold,
        max_chars=max_line_length,
    )
    out = postprocess(out)

    print(f"segments: {raw_count} raw Whisper segs → {len(out)} word-timestamp merged",
          file=sys.stderr)
    return out


def transcribe_faster(audio_path: str, model_name: str, language: str | None,
                      max_line_length: int, pause_threshold: float,
                      max_line_ms: int) -> list[dict]:
    from faster_whisper import WhisperModel

    models_dir = get_model_path()
    os.environ.setdefault("HF_HOME", models_dir)

    print(f"engine: faster-whisper", file=sys.stderr)
    print(f"model: {model_name}", file=sys.stderr)
    print(f"device: CPU", file=sys.stderr)
    print(f"transcribing: {audio_path}", file=sys.stderr)

    t0 = time.time()
    model = WhisperModel(model_name, device="cpu", compute_type="int8",
                         download_root=models_dir)
    seg_iter, info = model.transcribe(audio_path, language=language,
                                      word_timestamps=True)
    elapsed = time.time() - t0
    print(f"transcription took {elapsed:.1f}s", file=sys.stderr)
    print(f"detected language: {info.language}", file=sys.stderr)

    raw_segments = list(seg_iter)
    raw_count = len(raw_segments)

    if raw_segments and hasattr(raw_segments[0], 'words') and raw_segments[0].words:
        words = extract_words(raw_segments)
    else:
        words = []
        for seg in raw_segments:
            if seg.text.strip():
                words.append(Word(seg.start, seg.end, seg.text.strip()))

    out = merge_words_to_segments(
        words,
        max_line_ms=max_line_ms,
        pause_threshold=pause_threshold,
        max_chars=max_line_length,
    )
    out = postprocess(out)

    print(f"segments: {raw_count} raw Whisper segs → {len(out)} word-timestamp merged",
          file=sys.stderr)
    return out


# ── SRT writer ─────────────────────────────────────────────────────

def write_srt(segments: list[dict], output_path: str):
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")


# ── Platform detection ─────────────────────────────────────────────

def detect_platform() -> dict:
    return {"is_macos": sys.platform == "darwin",
            "is_arm": platform.machine() == "arm64"}


# ── Main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Transcribe audio to SRT using Whisper with word-level timestamp segmentation"
    )
    parser.add_argument("audio", help="audio file path (WAV 16kHz mono)")
    parser.add_argument("--output", "-o", help="output SRT path")
    parser.add_argument("--language", "-l", help="language code (e.g. zh, en)")
    parser.add_argument("--model", "-m", default=None,
                        help="model name (default: auto-select based on platform)")
    parser.add_argument("--max-line-length", type=int, default=40,
                        help="max characters per subtitle line (default: 40)")
    parser.add_argument("--max-line-ms", type=int, default=6000,
                        help="max duration per subtitle block in ms (default: 6000)")
    parser.add_argument("--pause-ms", type=int, default=None,
                        help="pause threshold for sentence split in ms (default: 300 zh / 500 en)")
    parser.add_argument("--engine", choices=["auto", "mlx", "faster-whisper"],
                        default="auto", help="force a specific engine")

    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"error: file not found {audio_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or str(audio_path.with_suffix(".srt"))

    lang = args.language
    pause_threshold = (args.pause_ms / 1000.0) if args.pause_ms is not None else get_pause_threshold(lang)

    plat = detect_platform()

    engine = args.engine
    if engine == "auto":
        if plat["is_macos"] and plat["is_arm"]:
            engine = "mlx"
        else:
            engine = "faster-whisper"

    model_name = args.model
    if not model_name:
        if engine == "mlx":
            model_name = "mlx-community/whisper-large-v3-turbo"
        else:
            model_name = "large-v3"

    if engine == "mlx" and not (plat["is_macos"] and plat["is_arm"]):
        print("warning: mlx engine requires macOS arm64, falling back to faster-whisper",
              file=sys.stderr)
        engine = "faster-whisper"
        model_name = model_name or "large-v3"

    seg_func = transcribe_mlx if engine == "mlx" else transcribe_faster
    segments = seg_func(
        str(audio_path), model_name, lang,
        args.max_line_length, pause_threshold, args.max_line_ms,
    )

    write_srt(segments, output_path)
    print(f"done! {len(segments)} segments -> {output_path}", file=sys.stderr)
    return output_path


if __name__ == "__main__":
    main()
