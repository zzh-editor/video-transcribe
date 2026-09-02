#!/usr/bin/env python3
"""
Evaluate subtitle segmentation quality against a manually refined gold SRT.

Compares two SRT files purely on line-break decisions (ignoring timestamps,
punctuation, and whitespace). Aligns the two normalized character streams with
SequenceMatcher, then counts, within matched blocks, how often both versions
break at the same character gap.

Metrics:
  recall    = breaks the algorithm shares with gold / total gold breaks
  precision = breaks the algorithm shares with gold / total algorithm breaks
  F1        = harmonic mean of recall & precision

Usage:
    python3 scripts/eval_segmentation.py <gold.srt> <candidate.srt>
"""

import argparse
import re
import sys
from difflib import SequenceMatcher


def text_lines(path: str) -> list[str]:
    """Extract subtitle text lines from an SRT file (paragraph per subtitle)."""
    with open(path, encoding="utf-8-sig") as f:
        content = f.read()
    return [
        m.strip()
        for m in re.findall(r'\d+\n[\d:,.]+\s*-->\s*[\d:,.]+\n(.+?)(?=\n\n|\Z)', content, re.S)
        if m.strip()
    ]


def normalize(text: str) -> str:
    """Keep only CJK letters / ASCII alphanumerics; drop all else."""
    return "".join(
        ch for ch in text
        if re.match(r'[\u4e00-\u9fff\u3400-\u4dbfa-zA-Z0-9]', ch)
    )


def build_stream(lines: list[str]):
    """Return (normalized concatenation, set of break indices).
    A break index i means position i in the stream ends a subtitle."""
    stream_parts: list[str] = []
    breaks: set[int] = set()
    pos = 0
    for line in lines:
        norm = normalize(line)
        stream_parts.append(norm)
        pos += len(norm)
        breaks.add(pos - 1)  # last char of this line is a segment end
    return "".join(stream_parts), breaks


def evaluate(gold: list[str], cand: list[str]):
    gold_stream, gold_breaks = build_stream(gold)
    cand_stream, cand_breaks = build_stream(cand)

    matcher = SequenceMatcher(a=gold_stream, b=cand_stream, autojunk=False)
    both = only_gold = only_cand = 0
    alignable = 0  # matched characters across both streams

    for i1, i2, size in matcher.get_matching_blocks():
        for k in range(size):
            g_break = (i1 + k) in gold_breaks
            c_break = (i2 + k) in cand_breaks
            alignable += 1
            if g_break and c_break:
                both += 1
            elif g_break:
                only_gold += 1
            elif c_break:
                only_cand += 1

    if alignable == 0:
        return None

    recall = both / max(both + only_gold, 1)
    precision = both / max(both + only_cand, 1)
    f1 = 2 * recall * precision / max(recall + precision, 1e-9)

    return {
        "gold_segments": len(gold),
        "cand_segments": len(cand),
        "gold_avg_chars": round(len(gold_stream) / len(gold), 1),
        "cand_avg_chars": round(len(cand_stream) / len(cand), 1),
        "aligned_chars": alignable,
        "both_breaks": both,
        "gold_only_breaks": only_gold,
        "cand_only_breaks": only_cand,
        "recall": round(recall, 3),
        "precision": round(precision, 3),
        "f1": round(f1, 3),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gold", help="gold/manual SRT")
    parser.add_argument("candidate", help="candidate SRT to evaluate")
    args = parser.parse_args()

    gold = text_lines(args.gold)
    cand = text_lines(args.candidate)
    if not gold or not cand:
        print("error: empty SRT input", file=sys.stderr)
        sys.exit(1)

    result = evaluate(gold, cand)
    if result is None:
        print("error: no alignable content", file=sys.stderr)
        sys.exit(1)

    print(f"gold      : {result['gold_segments']} segs, "
          f"avg {result['gold_avg_chars']} chars")
    print(f"candidate : {result['cand_segments']} segs, "
          f"avg {result['cand_avg_chars']} chars")
    print(f"aligned   : {result['aligned_chars']} chars, "
          f"breaks → both {result['both_breaks']} / "
          f"gold-only {result['gold_only_breaks']} / "
          f"cand-only {result['cand_only_breaks']}")
    print(f"recall    : {result['recall']}")
    print(f"precision : {result['precision']}")
    print(f"F1        : {result['f1']}")


if __name__ == "__main__":
    main()