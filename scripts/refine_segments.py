#!/usr/bin/env python3
"""
Refine SRT segments using word-level timestamps for scoring-driven segmentation.

Scoring engine replaces the old 6-level cascading semantic split + character-ratio
time allocation. Each gap between adjacent words is scored based on:
  - Punctuation at gap boundary
  - Silence/pause duration
  - Line length/time driver
  - Bad break penalties (dangling conjunctions, orphaned prepositions)
  - Fragmentation penalty

Pipeline:
  1. Clean empty / zero-duration / duplicate segments from upstream ASR
  2. Score-based segmentation using word timestamps
  3. Output segments with accurate per-word timing

Usage:
    python3 scripts/refine_segments.py <input.srt> [output.srt]
"""

import argparse
import re
import sys
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────

STRONG_PUNCT = frozenset("。！？.?!…")
WEAK_PUNCT = frozenset("，、；：,:;")

# Words that should NOT appear at the start of a subtitle line
# (prepositions/subordinating conjunctions that make viewers feel
#  the line starts mid-sentence)
BAD_LINE_START = frozenset({
    "在", "对", "给", "为", "把", "被", "从", "向",
    "和", "与", "跟", "同", "及",
    "关于", "对于", "根据", "经过", "通过", "除了",
    "因为", "但是", "所以", "不过", "然而", "而且", "并且",
    "如果", "虽然", "尽管", "由于", "为了", "除非",
    "当", "随着", "作为",
    "那", "那么",
    "for", "to", "with", "about", "because", "but", "so",
    "and", "then", "however", "although",
})

# Words that should NOT appear at the end of a subtitle line
# (dangling conjunctions)
BAD_LINE_END = frozenset({
    "因为", "但是", "所以", "然后", "不过",
    "然而", "而且", "并且",
    "如果", "虽然", "尽管", "由于",
    "because", "but", "so", "and", "then", "however",
})

# Words that are GOOD to have at line start
# (topic markers / teaching discourse markers)
GOOD_LINE_START = frozenset({
    "首先", "其次", "然后", "接着",
    "另外", "还有", "此外", "同样",
    "比如说", "举个例子", "说白了",
    "也就是说", "所以说",
    "我们来", "我们再来", "来看一下",
    "到时候", "有时候", "接下来",
})

_DEFAULT_MAX_CHARS = 25
_DEFAULT_MAX_LINE_MS = 4000
_DEFAULT_MIN_LINE_CHARS = 8


# ── Helpers ──────────────────────────────────────────────────────────

def _chars(words: list[dict]) -> int:
    """Count visual characters, excluding spaces and zero-width spaces."""
    return sum(
        len(w.get("word", "").replace(" ", "").replace("\u200b", ""))
        for w in words
    )


def _to_segment(words: list[dict]) -> dict:
    """Build a segment dict from a slice of word timestamps."""
    return {
        "start": words[0]["start"],
        "end": words[-1]["end"],
        "text": "".join(w.get("word", "") for w in words).strip(),
        "words": words,
    }


def _detect_language(words: list[dict]) -> str:
    """Quick language detection from words. Returns 'zh' or 'en'."""
    for w in words:
        for ch in w.get("word", ""):
            if '\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf':
                return "zh"
    return "en"


def _clean_words(words: list[dict]) -> list[dict]:
    """Remove malformed word entries (empty text, missing/zero timestamps)."""
    clean = []
    for w in words:
        text = w.get("word", "").strip()
        start = w.get("start")
        end = w.get("end")
        if not text or start is None or end is None:
            continue
        if end <= start:
            continue
        clean.append(w)
    return clean


# ── Scoring ─────────────────────────────────────────────────────────

def _score_gap(
    left: dict, right: dict,
    line_chars: int, line_dur: float,
    max_chars: int, max_dur: float,
    pause_threshold: float,
) -> float:
    """Score a gap between two words as a candidate split point.
    Higher score = better place to split.
    """
    score = 0.0
    left_word = left.get("word", "").strip()
    right_word = right.get("word", "").strip().lower()

    # 1. Punctuation bonus
    if left_word and left_word[-1] in STRONG_PUNCT:
        score += 5.0
    elif left_word and left_word[-1] in WEAK_PUNCT:
        score += 3.0

    # 2. Silence/pause bonus
    gap = right.get("start", 0) - left.get("end", 0)
    if gap >= pause_threshold:
        score += 4.0
    elif gap >= pause_threshold * 0.5:
        score += 1.0

    # 3. Length driver — line getting long, encourage a break
    if line_dur > max_dur or line_chars > max_chars:
        score += 2.0

    # 4. Right-side word is bad at line start → penalty
    if right_word in BAD_LINE_START:
        score -= 4.0
    #    Right-side word is good at line start → bonus
    elif right_word in GOOD_LINE_START:
        score += 1.0

    # 5. Left-side word is bad at line end → penalty
    if left_word.lower() in BAD_LINE_END:
        score -= 4.0

    # 6. Fragmentation penalty — line too short without strong signal
    if line_chars < _DEFAULT_MIN_LINE_CHARS and score < 3:
        score -= 2.0

    return score


# ── Segmentation engine ─────────────────────────────────────────────

def _segment_words(
    words: list[dict],
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_dur: float = _DEFAULT_MAX_LINE_MS / 1000.0,
    pause_threshold: float = 0.3,
) -> list[dict]:
    """
    Score-driven subtitle segmentation with natural break detection.

    Two-phase approach:
      1. Pre-compute 'natural break' positions — gaps with strong
         punctuation or significant pause. These are preferred split points.
      2. Walk through words; split at the best available point when
         constraints are exceeded OR when a natural break provides a
         well-sized line.
    """
    if not words:
        return []

    n = len(words)

    # Phase 1: pre-compute natural break positions
    natural_breaks: set[int] = set()
    for i in range(1, n):
        gap = words[i]["start"] - words[i - 1]["end"]
        left_char = words[i - 1]["word"].strip()[-1:] if words[i - 1]["word"].strip() else ""
        if (left_char and left_char[0] in STRONG_PUNCT) or gap >= pause_threshold:
            natural_breaks.add(i)

    lines: list[dict] = []
    start = 0

    while start < n:
        best_score = -999.0
        best_end = start + 1
        pending_break = None  # natural break too early; wait for line to grow

        for end in range(start + 1, n + 1):
            chunk = words[start:end]
            chunk_chars = _chars(chunk)
            chunk_dur = chunk[-1]["end"] - chunk[0]["start"]

            # Record first natural break position regardless of line size
            if end in natural_breaks and pending_break is None:
                pending_break = end

            # Use pending break when line is substantial and within limits
            if pending_break is not None:
                pre_chars = _chars(words[start:pending_break])
                pre_dur = words[pending_break - 1]["end"] - words[start]["start"]
                if pre_chars >= max_chars // 2 and pre_chars <= max_chars and pre_dur <= max_dur:
                    best_end = pending_break
                    break

            # Still within limits — keep accumulating
            if chunk_chars <= max_chars and chunk_dur <= max_dur:
                best_end = end
                continue

            # Overflow: evaluate all possible cuts
            for cut in range(start + 1, end):
                pre = words[start:cut]
                pre_chars = _chars(pre)
                if pre_chars < 1:
                    continue
                s = _score_gap(
                    words[cut - 1], words[cut],
                    pre_chars,
                    pre[-1]["end"] - pre[0]["start"],
                    max_chars, max_dur, pause_threshold,
                )
                if s >= best_score:
                    best_score = s
                    best_end = cut

            # If overflow has a scored candidate, use it
            if best_score <= -999.0 and pending_break is not None:
                best_end = pending_break
            break

        # Safety: always advance at least one word
        if best_end <= start:
            best_end = start + 1

        lines.append(_to_segment(words[start:best_end]))
        start = best_end

    return lines


# ── ASR cleanup ──────────────────────────────────────────────────────

def _clean_segments(segments: list[dict]) -> list[dict]:
    """Filter out ASR noise: empty text, zero duration, exact duplicates."""
    seen: set[tuple[str, float]] = set()
    clean: list[dict] = []
    for seg in segments:
        text = seg.get("text", "").strip()
        if not text:
            continue
        if seg.get("end", 0) <= seg.get("start", 0):
            continue
        key = (text, round(seg["start"], 3))
        if key in seen:
            continue
        seen.add(key)
        clean.append(seg)
    return clean


# ── Fallback (for CLI / segments without word timestamps) ───────────

def _fallback_split(
    seg: dict,
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_line_ms: int = _DEFAULT_MAX_LINE_MS,
) -> list[dict]:
    """Simple punctuation+ratio split for segments without word timestamps."""
    text = seg.get("text", "").strip()
    if not text:
        return []

    chars = len(text.replace(" ", ""))
    duration = seg["end"] - seg["start"]
    max_dur = max_line_ms / 1000.0

    if chars <= max_chars and duration <= max_dur:
        return [seg]

    n_pieces = max(
        (chars + max_chars - 1) // max_chars,
        int(duration / max_dur) + 1,
        1,
    )
    result = []
    char_pos = 0
    start_time = seg["start"]
    target_chars = chars // n_pieces

    for i in range(n_pieces):
        if i == n_pieces - 1:
            piece_text = text[char_pos:].strip()
        else:
            end_char = min(char_pos + target_chars, len(text))
            for c in range(end_char, max(char_pos, end_char - 6), -1):
                c = min(c, len(text) - 1)
                if c > char_pos and text[c:c+1] in "，、。？！；：":
                    end_char = c + 1
                    break
            piece_text = text[char_pos:end_char].strip()
            char_pos = end_char

        if piece_text:
            ratio = len(piece_text.replace(" ", "")) / max(chars, 1)
            end_time = start_time + duration * ratio
            result.append({
                "start": start_time, "end": end_time,
                "text": piece_text, "words": [],
            })
            start_time = end_time

    return result


# ── Main refine pipeline ────────────────────────────────────────────

def refine(
    segments: list[dict],
    max_chars: int = _DEFAULT_MAX_CHARS,
    max_line_ms: int = _DEFAULT_MAX_LINE_MS,
) -> list[dict]:
    """
    Refine ASR segments into well-timed subtitles using scoring-driven
    segmentation with word-level timestamps.

    Args:
        segments: List of segment dicts with 'start', 'end', 'text', 'words'
        max_chars: Max characters per subtitle line
        max_line_ms: Max duration per subtitle line in milliseconds

    Returns:
        Refined segment list with accurate per-line timing
    """
    if not segments:
        return []

    segments = _clean_segments(segments)
    max_dur = max_line_ms / 1000.0

    out: list[dict] = []
    for seg in segments:
        words = seg.get("words")
        if not words:
            # No word timestamps — fallback for CLI usage
            out.extend(_fallback_split(seg, max_chars, max_line_ms))
        else:
            words = _clean_words(words)
            if len(words) < 2:
                out.append(_to_segment(words) if words else seg)
                continue
            lang = _detect_language(words)
            pause_th = 0.3 if lang == "zh" else 0.5
            out.extend(_segment_words(words, max_chars, max_dur, pause_th))

    return out


# ── SRT I/O (CLI only) ──────────────────────────────────────────────

def parse_srt(path: str) -> list[dict]:
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
            text_lines: list[str] = []
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
                    "start": start, "end": end, "text": text, "words": [],
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


# ── CLI ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Refine SRT segments using word-level timestamps"
    )
    parser.add_argument("input", help="input SRT file path")
    parser.add_argument("output", nargs="?",
                        help="output SRT file path (default: overwrite input)")
    parser.add_argument("--max-chars", type=int, default=_DEFAULT_MAX_CHARS,
                        help=f"max characters per line (default: {_DEFAULT_MAX_CHARS})")
    parser.add_argument("--max-line-ms", type=int, default=_DEFAULT_MAX_LINE_MS,
                        help=f"max duration per line in ms (default: {_DEFAULT_MAX_LINE_MS})")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    segs = parse_srt(str(input_path))
    if not segs:
        print(f"error: no valid segments in {input_path}", file=sys.stderr)
        sys.exit(1)

    original_count = len(segs)
    out = refine(segs, max_chars=args.max_chars, max_line_ms=args.max_line_ms)
    output_path = args.output or str(input_path)
    write_srt(out, output_path)
    print(
        f"refined {original_count} → {len(out)} segments "
        f"(max_chars={args.max_chars}) → {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
