#!/usr/bin/env python3
"""Re-run Groq post-processing from a saved raw response — zero API cost.

transcribe.py dumps the raw Groq response to <output>.raw.json and the VAD
speech map to <output>.vad.json. This script feeds them back through the
exact zh post-processing chain (clean → merge → word-adapter refine →
cleanup → re-attach words) so segmentation/cleanup parameter changes can be
evaluated without another paid transcription call.

Usage:
    python3 scripts/replay_postprocess.py <output_dir>/raw.srt.raw.json \
        [--vad <output_dir>/raw.srt.vad.json] [--max-line-length 15] \
        [--max-line-ms 3000] [--out <output_dir>/replay.srt]

Expects the sibling .vad.json by default (same stem as the raw json).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cleanup_segments import cleanup as cleanup_segs
from groq_word_adapter import _assign_words_to_segments, refine_groq_segments
from refine_segments import _clean_segments, _merge_fragments
from transcribe import export_cleanup_audit, export_refined_json, write_srt


def replay(raw_path: str, vad_path: str | None, out_path: str,
           max_line_length: int, max_line_ms: int, pause_s: float) -> list[dict]:
    with open(raw_path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    segments = [s for s in raw.get("segments", []) if s.get("text", "").strip()]
    top_words = raw.get("words", [])

    regions = None
    if vad_path and Path(vad_path).exists():
        from vad_map import SpeechRegions
        with open(vad_path, "r", encoding="utf-8") as f:
            regions = SpeechRegions.from_map(json.load(f))

    segments = _clean_segments(segments)
    if len(segments) > 1:
        segments = _merge_fragments(segments, max_line_length * 2)
    segments = refine_groq_segments(
        segments, top_words,
        max_chars=max_line_length, max_line_ms=max_line_ms,
        pause_threshold=pause_s, regions=regions,
    )
    if len(segments) > 1:
        segments = _merge_fragments(segments, max_line_length * 2)
    audit: list[dict] = []
    segments = cleanup_segs(segments, audit=audit, regions=regions)
    if top_words:
        for seg, ws in zip(segments, _assign_words_to_segments(top_words, segments)):
            if ws:
                seg["words"] = ws

    write_srt(segments, out_path)
    export_refined_json(segments, out_path + ".words.json")
    export_cleanup_audit(audit, out_path)
    print(f"replay: {len(segments)} segments -> {out_path}", file=sys.stderr)
    return segments


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("raw_json", help="path to <output>.raw.json")
    parser.add_argument("--vad", default=None,
                        help="VAD map json (default: sibling .vad.json)")
    parser.add_argument("--out", default=None, help="output SRT path")
    parser.add_argument("--max-line-length", type=int, default=15)
    parser.add_argument("--max-line-ms", type=int, default=3000)
    parser.add_argument("--pause-ms", type=int, default=150)
    args = parser.parse_args()

    raw = Path(args.raw_json)
    if not raw.exists():
        print(f"error: {raw} not found", file=sys.stderr)
        return 1
    vad = args.vad or str(raw.with_suffix("").with_suffix(".vad.json"))
    out = args.out or str(raw.with_suffix("").with_suffix(".replay.srt"))
    replay(str(raw), vad, out, args.max_line_length, args.max_line_ms,
           args.pause_ms / 1000.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
