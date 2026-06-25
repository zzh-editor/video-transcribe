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

KNOWN_HALLUCINATIONS = frozenset({
    "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
})


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
                 and s["text"].strip() not in KNOWN_HALLUCINATIONS]

    # Step 2: merge consecutive segments with identical text (case-insensitive)
    merged: list[dict] = [non_empty[0]]
    for s in non_empty[1:]:
        last = merged[-1]
        if s["text"].strip().lower() == last["text"].strip().lower():
            last["end"] = s["end"]
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
