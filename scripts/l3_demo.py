#!/usr/bin/env python3
"""
Simulate L3 LLM semantic re-segmentation demo.

Reads refined_segments.json (exported by transcribe.py --export-refined),
splits chosen segments at specified word indices, and writes a new SRT.
Pure demonstration of what the Agent does manually in the L3 checkpoint.

Usage:
    python3 scripts/l3_demo.py refined_segments.json splits.json -o out.srt

splits.json format: {"<1-based seg#>": [<0-based word index>, ...]}
Each split index points at the first word of a new sub-segment.
"""

import argparse
import json


def format_ts(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("segments_json")
    parser.add_argument("splits_json")
    parser.add_argument("-o", "--output", required=True)
    args = parser.parse_args()

    segs = json.load(open(args.segments_json, encoding="utf-8"))
    splits = json.load(open(args.splits_json, encoding="utf-8"))

    out = []
    for idx, seg in enumerate(segs, 1):
        text = seg["text"]
        words = seg.get("words", [])
        cut = splits.get(str(idx)) or []
        if not cut or not words:
            out.append(seg)
            continue
        # Build sub-segments from word timestamps
        marks = sorted(set(cut))
        prev = 0
        pieces = []
        for m in marks + [len(words)]:
            piece_words = words[prev:m]
            prev = m
            if not piece_words:
                continue
            pieces.append({
                "start": piece_words[0]["start"],
                "end": piece_words[-1]["end"],
                "text": "".join(w["word"] for w in piece_words).strip(),
            })
        # Verify char conservation
        joined = "".join(w["word"] for w in words)
        rebuilt = "".join(p["text"] for p in pieces)
        if rebuilt.replace(" ", "") != joined.replace(" ", ""):
            print(f"warning: #{idx} char mismatch", file=__import__("sys").stderr)
        out.extend(pieces)

    with open(args.output, "w", encoding="utf-8") as f:
        for i, seg in enumerate(out, 1):
            f.write(f"{i}\n")
            f.write(f"{format_ts(seg['start'])} --> {format_ts(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")
    print(f"wrote {len(out)} segments -> {args.output}")


if __name__ == "__main__":
    main()