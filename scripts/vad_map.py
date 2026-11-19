#!/usr/bin/env python3
"""Local Silero VAD speech map for long audio.

Whisper hallucinates text into long silences, and Groq's word timestamps
stretch across gaps so word-gap detection under-reports real pauses. A local
ONNX Silero VAD pass (no torch, ~2MB model) gives the ground truth: which
time ranges carry speech and which are silence.

Output JSON (``vad.json``) consumed by:
  - groq_word_adapter: VAD gaps force-split segments whose word gaps collapsed
  - groq_word_adapter: crumb merge decisions use the real speech-to-speech gap
  - cleanup_segments: segments with no speech overlap are silence hallucinations

Degrades to None on any failure (missing deps, decode error); callers fall
back to word-gap heuristics.

Usage:
    python3 scripts/vad_map.py <audio> [-o out.json]
"""

import argparse
import json
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path

# Subtitle-relevant gaps only: pauses shorter than this are prosody, not a
# sentence boundary, so speech regions separated by less are merged into one.
DEFAULT_MIN_GAP_S = 0.6


def _decode_to_16k_mono(audio_path: str) -> str:
    """ffmpeg → 16kHz mono s16 WAV. Returns the temp wav path."""
    import os
    import subprocess
    import tempfile

    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="vad_")
    os.close(fd)
    try:
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", audio_path,
             "-vn", "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", "-f", "wav", tmp],
            check=True,
        )
    except Exception:
        import os as _os
        _os.unlink(tmp)
        raise
    return tmp


def _read_wav_mono(path: str):
    """stdlib wave + numpy read → (float32 array in [-1,1], sample_rate).

    _decode_to_16k_mono guarantees s16 samples, so no width switch is needed."""
    import wave

    import numpy as np

    with wave.open(path, "rb") as wf:
        rate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    return audio, rate


def build_vad_map(audio_path: str, min_gap_s: float = DEFAULT_MIN_GAP_S,
                  threshold: float = 0.5) -> dict | None:
    """Run Silero VAD over the whole file. Returns None on any failure."""
    wav_path: str | None = None
    try:
        from silero_vad_notorch import get_speech_timestamps, load_silero_vad

        wav_path = _decode_to_16k_mono(audio_path)
        audio, rate = _read_wav_mono(wav_path)
        if audio.size == 0:
            return None
        model = load_silero_vad(onnx=True)
        speech = get_speech_timestamps(
            audio, model,
            sampling_rate=rate,
            threshold=threshold,
            min_speech_duration_ms=250,
            min_silence_duration_ms=int(min_gap_s * 1000),
            speech_pad_ms=30,
            return_seconds=True,
        )
        if not speech:
            return None
        # Collapse sub-threshold pauses the model still emitted.
        regions = [(speech[0]["start"], speech[0]["end"])]
        for s in speech[1:]:
            if s["start"] - regions[-1][1] < min_gap_s:
                regions[-1] = (regions[-1][0], max(regions[-1][1], s["end"]))
            else:
                regions.append((s["start"], s["end"]))
        duration = float(audio.size) / rate
        gaps = [(regions[i - 1][1], regions[i][0])
                for i in range(1, len(regions))
                if regions[i][0] - regions[i - 1][1] >= _SPLIT_GAP_S]
        speech_s = sum(e - s for s, e in regions)
        return {
            "duration": round(duration, 2),
            "speech": [{"start": round(s, 3), "end": round(e, 3)} for s, e in regions],
            "speech_ratio": round(speech_s / duration, 4) if duration else 0.0,
            "gap_count": len(gaps),
            "largest_gaps": [{"start": round(g0, 3), "end": round(g1, 3),
                              "dur": round(g1 - g0, 3)}
                             for g0, g1 in sorted(gaps, key=lambda g: g[1] - g[0],
                                                   reverse=True)[:10]],
        }
    except Exception as exc:
        print(f"warning: VAD map build failed ({exc})", file=sys.stderr)
        return None
    finally:
        if wav_path is not None:
            Path(wav_path).unlink(missing_ok=True)


# Gaps shorter than this are not subtitle sentence boundaries (aligns with
# groq_word_adapter._SILENCE_SPLIT_GAP_S = 0.80).
_SPLIT_GAP_S = 0.8


class SpeechRegions:
    """Query helpers over the VAD speech timeline. All times in seconds."""

    def __init__(self, speech: list):
        self._sp = sorted((float(s), float(e)) for s, e in speech)
        self._starts = [s for s, _ in self._sp]

    @classmethod
    def from_map(cls, vad_map: dict) -> "SpeechRegions":
        return cls([(r["start"], r["end"]) for r in vad_map["speech"]])

    def speech_in(self, t0: float, t1: float) -> list:
        """Regions overlapping [t0, t1], as (start, end) tuples."""
        i = bisect_left(self._starts, t1)
        out = []
        for s, e in self._sp[:i]:
            if e > t0:
                out.append((max(s, t0), min(e, t1)))
        return out

    def overlap(self, t0: float, t1: float) -> float:
        """Total seconds of speech inside [t0, t1]."""
        return sum(e - s for s, e in self.speech_in(t0, t1))

    def distance_to_speech(self, t: float) -> float:
        """Seconds from time t to the nearest speech region (0 when inside)."""
        i = bisect_right(self._starts, t)
        best = None
        if i > 0:
            s, e = self._sp[i - 1]
            best = 0.0 if t <= e else t - e
        if i < len(self._sp):
            s, _ = self._sp[i]
            gap = max(0.0, s - t)
            best = gap if best is None else min(best, gap)
        return float("inf") if best is None else best

    def internal_gaps(self, t0: float, t1: float, min_len: float = _SPLIT_GAP_S) -> list:
        """Silence gaps strictly inside [t0, t1] longer than min_len."""
        out = []
        sp = self.speech_in(t0, t1)
        if len(sp) < 2:
            return out
        for i in range(1, len(sp)):
            g0, g1 = sp[i - 1][1], sp[i][0]
            if g1 - g0 >= min_len:
                out.append((g0, g1))
        return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", help="audio/video file path")
    parser.add_argument("-o", "--output", default=None,
                        help="output JSON path (default: <audio>.vad.json)")
    args = parser.parse_args()

    src = Path(args.audio)
    if not src.exists():
        print(f"error: file not found {src}", file=sys.stderr)
        return 1

    vad_map = build_vad_map(str(src))
    if vad_map is None:
        print("error: no speech detected or VAD unavailable", file=sys.stderr)
        return 1

    out_path = args.output or str(src.with_suffix(".vad.json"))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(vad_map, f, ensure_ascii=False, indent=1)

    dur = vad_map["duration"]
    print(f"vad: {dur:.0f}s total, {vad_map['speech_ratio']*100:.1f}% speech, "
          f"{len(vad_map['speech'])} regions, {vad_map['gap_count']} gaps >= {_SPLIT_GAP_S}s",
          file=sys.stderr)
    for g in vad_map["largest_gaps"][:5]:
        print(f"  gap {g['start']:.1f}s → {g['end']:.1f}s ({g['dur']:.1f}s)", file=sys.stderr)
    print(f"vad map -> {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
