#!/usr/bin/env python3
"""
Clean up SRT segments: remove empty-text entries and merge consecutive
duplicate-text entries (artifacts from VAD boundary overlap or refine).

Pipeline:
  1. Drop segments with empty/whitespace-only text
  2. Merge adjacent segments sharing identical text (extend the first's end time)
  3. Re-number

Usage:
    python3 scripts/cleanup_segments.py <input.srt> [output.srt]
"""

import argparse
import sys
from collections import Counter
from pathlib import Path


# ── Hallucination detection ──────────────────────────────────────────

MIN_HALLUCINATION_LEN = 30   # minimum length to check for loops
MIN_UNIQUE_CHARS_RATIO = 0.04  # unique chars / total length below this = loop

# Known whisper hallucination phrases. Matching is normalized (spaces/zero-width
# stripped) so Groq's spacing-free output still hits the blacklist.
KNOWN_HALLUCINATIONS = frozenset({
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
    # Whisper re-reads a Chinese instruction prompt aloud as hallucinated speech
    # during silent windows. Substrings match any variant of the recurring
    # phrase ("请准确转写专业术语．保持简体中文。" / "请准确转写专业术" etc).
    "请准确转写专业",
    "保持简体中文",
})

# Text-density hallucination rule: a segment whose text is far too long for
# its tiny duration is almost always a whisper hallucination (real speech
# sustains ~4-8 chars/s in Chinese). e.g. 18 chars in 0.54s (~33 chars/s).
HALLUC_DURATION_S = 1.0       # only flag segments shorter than this
HALLUC_MIN_CHARS = 15         # ... with at least this many visible chars
HALLUC_MIN_CHARS_PER_SEC = 15.0  # ... at a density above this threshold
# English sustains far higher char density than Chinese — ~15-25 chars/s at
# natural fast speech (English words average ~5 chars). The 15 chars/s rule
# wrongly flags rapid but real English (e.g. "find this blue dot." = 20 chars/s
# in 0.79s). Latin-only segments get a higher bar; CJK keeps the tighter one.
HALLUC_MIN_CHARS_PER_SEC_EN = 25.0  # Latin-only density bar (still catches >25/s)

# Sparse-window hallucination rule (the mirror of density): a very long
# segment carrying almost no text is Whisper inflating a single repeated
# phrase (e.g. "谢谢大家") across a trailing silence window. Human speech
# sustains ~4-8 chars/s, so a 30s window with 4 chars is a fabricated loop.
SPARSE_DURATION_S = 20.0      # only flag segments longer than this
SPARSE_MAX_CHARS_PER_SEC = 0.5  # ... with density below this (chars / second)

# Groq verbose_json quality-metric hallucination rule. Whisper reports a high
# no_speech_prob for silence-blocks it hallucinated text into; low avg_logprob
# additionally flags low-confidence decoding. Together they identify invented
# content better than any text heuristic (which can be fooled by spacing or
# short-but-valid loops).
NO_SPEECH_PROB_THRESHOLD = 0.8   # >= this → decoder believes there's no speech
AVG_LOGPROB_THRESHOLD = -1.0     # <= this → low decoding confidence
MIN_HALLUC_TEXT_CHARS = 4        # ignore near-empty segments (handled elsewhere)


def _normalize_text(text: str) -> str:
    return text.replace(" ", "").replace("\u200b", "").lower()


def _is_known_hallucination(text: str) -> bool:
    """Blacklist match, normalized (space-insensitive, case-insensitive)."""
    norm = _normalize_text(text)
    if not norm:
        return False
    for phrase in KNOWN_HALLUCINATIONS:
        if _normalize_text(phrase) in norm:
            return True
    return False


def _is_repeat_loop(text: str) -> bool:
    """Detect VAD hallucination loops (repeating chars, bigrams, or tiny vocabulary)."""
    chars = text.replace(" ", "").replace("\u200b", "")
    if len(chars) < MIN_HALLUCINATION_LEN:
        return False

    n_unique = len(set(chars))
    # Very small vocabulary relative to length → repetition loop
    if n_unique / len(chars) < MIN_UNIQUE_CHARS_RATIO:
        return True
    # Very few unique characters overall → almost certainly a loop
    if n_unique <= 2 and len(chars) >= MIN_HALLUCINATION_LEN:
        return True

    return False


def _is_latin_text(text: str) -> bool:
    """True when the segment is essentially Latin-script speech (English), i.e.
    no CJK characters. Mixed bilingual segments keep the Chinese threshold."""
    for ch in text:
        if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
            return False
    return bool(text.strip())


def _is_dense_hallucination(seg: dict) -> bool:
    """True when a very short segment carries an implausible amount of text,
    i.e. text density far exceeds human speech rate. The density bar is
    language-aware: English sustains ~15-25 chars/s naturally, so Latin-only
    segments use a higher threshold than Chinese (4-8 chars/s)."""
    text = seg.get("text", "").strip()
    if not text:
        return False
    threshold = HALLUC_MIN_CHARS_PER_SEC_EN if _is_latin_text(text) else HALLUC_MIN_CHARS_PER_SEC
    chars = len(_normalize_text(text))
    if not chars or chars < HALLUC_MIN_CHARS:
        return False
    duration = (seg.get("end", 0) or 0) - (seg.get("start", 0) or 0)
    if duration <= 0 or duration > HALLUC_DURATION_S:
        return False
    return chars / duration >= threshold


def _is_sparse_hallucination(seg: dict) -> bool:
    """True when a very long segment carries implausibly little text.

    Whisper inflates a single repeated phrase (e.g. "谢谢大家") across a
    trailing silence window, producing e.g. 4 chars over 30s. Real speech
    sustains ~4-8 chars/s, so density far below that over a long window is
    a fabricated loop, not a slow speaker.
    """
    text = seg.get("text", "").strip()
    chars = len(_normalize_text(text))
    if not chars:
        return False
    duration = (seg.get("end", 0) or 0) - (seg.get("start", 0) or 0)
    if duration < SPARSE_DURATION_S:
        return False
    return chars / duration < SPARSE_MAX_CHARS_PER_SEC


def _has_quality_metrics(seg: dict) -> bool:
    """True when Groq's verbose_json returned quality metrics for this seg."""
    return "no_speech_prob" in seg and "avg_logprob" in seg


def _is_no_speech_hallucination(seg: dict) -> bool:
    """Detect hallucinated content via Groq's decoder confidence metrics.

    Whisper fabricates text into silence blocks and marks them with a high
    no_speech_prob plus a very negative avg_logprob. When both signals agree
    we drop the segment regardless of what its text looks like — this catches
    spacing/blacklist-free hallucinations the text heuristics miss.
    """
    if not _has_quality_metrics(seg):
        return False
    nsp = seg.get("no_speech_prob", 0) or 0
    if nsp < NO_SPEECH_PROB_THRESHOLD:
        return False
    logprob = seg.get("avg_logprob", 0) or 0
    if logprob > AVG_LOGPROB_THRESHOLD:
        return False
    text = seg.get("text", "").strip()
    if len(_normalize_text(text)) < MIN_HALLUC_TEXT_CHARS:
        return False
    return True


def cleanup(segments: list[dict]) -> list[dict]:
    if not segments:
        return []

    # Step 1: remove empty-text segments
    non_empty = [s for s in segments if s.get("text", "").strip()]
    if not non_empty:
        return []

    # Step 1.5: remove hallucinated segments
    non_empty = [s for s in non_empty
                 if not _is_repeat_loop(s.get("text", ""))
                 and not _is_known_hallucination(s.get("text", ""))
                 and not _is_dense_hallucination(s)
                 and not _is_sparse_hallucination(s)
                 and not _is_no_speech_hallucination(s)]
    if not non_empty:
        return []

    # Step 2: merge consecutive segments with identical text (case-insensitive)
    # Only when gap <= 1.0s — covers VAD boundary overlap without swallowing
    # distant repetitions or stretching across silent windows.
    MAX_DUP_MERGE_GAP_S = 1.0
    merged: list[dict] = [non_empty[0]]
    for s in non_empty[1:]:
        last = merged[-1]
        if s["text"].strip().lower() == last["text"].strip().lower():
            gap = s.get("start", 0) - last.get("end", 0)
            if gap <= MAX_DUP_MERGE_GAP_S:
                last["end"] = s["end"]
            else:
                merged.append(s)
        else:
            merged.append(s)

    # Step 3: re-number via caller responsibility (already handled by SRT writer)
    return merged


def parse_srt(path: str) -> list[dict]:
    import re
    segments: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        if line.isdigit():
            i += 1
            if i >= len(lines):
                break
            ts = lines[i].strip()
            i += 1
            text_lines = []
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            text = " ".join(text_lines)
            m = re.match(
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
                r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})",
                ts,
            )
            if m:
                start = (int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
                         + int(m[4]) / 1000)
                end = (int(m[5]) * 3600 + int(m[6]) * 60 + int(m[7])
                       + int(m[8]) / 1000)
                segments.append({
                    "start": start,
                    "end": end,
                    "text": text,
                })
            i += 1
        else:
            i += 1
    return segments


def write_srt(segments: list[dict], path: str):
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = seg["start"]
            end = seg["end"]
            sh = int(start // 3600)
            sm = int((start % 3600) // 60)
            ss = int(start % 60)
            sms = int((start - int(start)) * 1000)
            eh = int(end // 3600)
            em = int((end % 3600) // 60)
            es = int(end % 60)
            ems = int((end - int(end)) * 1000)
            f.write(f"{i}\n")
            f.write(f"{sh:02d}:{sm:02d}:{ss:02d},{sms:03d} --> "
                    f"{eh:02d}:{em:02d}:{es:02d},{ems:03d}\n")
            f.write(f"{seg['text']}\n\n")


def main():
    parser = argparse.ArgumentParser(
        description="Clean up SRT: remove empty segments & merge adjacent duplicates"
    )
    parser.add_argument("input", help="input SRT file path")
    parser.add_argument("output", nargs="?",
                        help="output SRT file path (default: overwrite input)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    segs = parse_srt(str(input_path))
    before = len(segs)
    out = cleanup(segs)
    after = len(out)

    output_path = args.output or str(input_path)
    write_srt(out, output_path)

    removed = before - after
    if removed:
        print(f"cleanup: {before} → {after} segments (removed {removed} empty/duplicate) → {output_path}",
              file=sys.stderr)
    else:
        print(f"cleanup: {before} segments, no changes → {output_path}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
