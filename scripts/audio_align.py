#!/usr/bin/env python3
"""Align SRT cue boundaries to actual speech using an RMS energy envelope.

Groq returns word timestamps that swallow the silences between a phrase and
whatever the speaker does next (demo, screen switching, thinking). The result
is a caption that stays on screen for 20s while nobody talks. This module
computes a 0.2s RMS envelope from the audio and shrinks each cue's silent
head/tail, keeping the middle untouched so mid-cue pauses survive.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess

import numpy as np

WINDOW_S = 0.2          # RMS resolution
MIN_SHRINK_S = 0.8      # only trim a silent edge that wastes at least this much
PAD_S = 0.25            # keep a little silence around the speech
SPEECH_ABOVE_DB = 10.0  # speech threshold relative to the measured noise floor
MIN_DUR_S = 0.3


def rms_envelope(path: str, window_s: float = WINDOW_S) -> tuple[np.ndarray, float]:
    """Return (dB envelope, noise floor dB) for the given audio file."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "quiet", "-i", path, "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
        capture_output=True, check=True,
    )
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    win = int(16000 * window_s)
    n = len(samples) // win
    if n == 0:
        return np.zeros(0), -60.0
    rms = np.sqrt((samples[: n * win].reshape(n, win) ** 2).mean(axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    return db, float(np.percentile(db, 10))


def align_segment(seg: dict, db: np.ndarray, threshold_db: float) -> dict:
    """Shrink one cue's silent head/tail to the speech it actually covers."""
    start, end = float(seg.get("start", 0)), float(seg.get("end", 0))
    i0 = max(0, int(start / WINDOW_S))
    i1 = min(len(db), int(end / WINDOW_S))
    if i1 <= i0:
        return seg
    speech = np.nonzero(db[i0:i1] > threshold_db)[0]
    if len(speech) == 0:
        return seg

    first_t = (i0 + speech[0]) * WINDOW_S
    last_t = (i0 + speech[-1] + 1) * WINDOW_S

    new_start, new_end = start, end
    if first_t - start >= MIN_SHRINK_S:
        new_start = max(start, first_t - PAD_S)
    if end - last_t >= MIN_SHRINK_S:
        new_end = min(end, last_t + PAD_S)
    if new_end - new_start < MIN_DUR_S:
        return seg
    if new_start == start and new_end == end:
        return seg
    out = dict(seg)
    out["start"], out["end"] = round(new_start, 3), round(new_end, 3)
    return out


def align_segments(segments: list[dict], audio_path: str) -> tuple[list[dict], list[dict]]:
    """Align every cue and fix overlaps. Returns (segments, report)."""
    db, noise = rms_envelope(audio_path)
    if len(db) == 0:
        return segments, []
    threshold = noise + SPEECH_ABOVE_DB

    aligned = [align_segment(s, db, threshold) for s in segments]
    # A cue trimmed into its neighbour would double-show text; keep the timeline
    # monotonic by clipping the earlier cue at the later one.
    for prev, cur in zip(aligned, aligned[1:]):
        if cur["start"] < prev["end"]:
            prev["end"] = round(max(cur["start"], prev["start"] + MIN_DUR_S), 3)

    report = []
    for before, after in zip(segments, aligned):
        if abs(before["start"] - after["start"]) > 1e-6 or abs(before["end"] - after["end"]) > 1e-6:
            report.append({
                "before": [before["start"], before["end"]],
                "after": [after["start"], after["end"]],
                "saved_s": round((before["end"] - before["start"]) - (after["end"] - after["start"]), 2),
                "text": before.get("text", ""),
            })
    return aligned, report


SRT_TIME = re.compile(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)")


def parse_srt(path: str) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    segments = []
    for block in content.strip().split("\n\n"):
        lines = block.strip().split("\n")
        if len(lines) < 2 or not SRT_TIME.search(lines[1]):
            continue
        m = SRT_TIME.search(lines[1])
        s = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4)) / 1000
        e = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) + int(m.group(8)) / 1000
        segments.append({"start": s, "end": e, "text": "\n".join(lines[2:])})
    return segments


def format_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(segments: list[dict], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("srt")
    ap.add_argument("audio")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--report")
    args = ap.parse_args()

    segments = parse_srt(args.srt)
    aligned, report = align_segments(segments, args.audio)
    write_srt(aligned, args.output)
    print(f"aligned {len(segments)} cues -> {args.output} ({len(report)} trimmed)")
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
