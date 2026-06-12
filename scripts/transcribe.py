#!/usr/bin/env python3
"""
Transcribe audio to SRT using FunASR AutoModel (Fun-ASR-Nano + VAD + punctuation).

Usage:
    python3 scripts/transcribe.py <audio.wav> [--output <raw.srt>] [--language zh] [--device cpu]

Requires: pip install funasr
"""

import argparse
import json
import sys
from pathlib import Path


def format_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def extract_segments(result):
    """
    Extract timed segments from FunASR result, handling various output formats.

    Returns list of {"start": float, "end": float, "text": str}.
    """
    if isinstance(result, list):
        result = result[0] if result else {}
    if not isinstance(result, dict):
        return []

    sentence_info = result.get("sentence_info", [])
    if sentence_info and isinstance(sentence_info, list):
        segments = []
        for s in sentence_info:
            text = s.get("text", "").strip()
            if text:
                segments.append({
                    "start": s.get("start", 0.0),
                    "end": s.get("end", 0.0),
                    "text": text,
                })
        if segments:
            return segments

    text = result.get("text", "").strip()
    timestamp = result.get("timestamp", []) or result.get("ts_list", [])
    if text and timestamp:
        return [
            {"start": ts[0], "end": ts[1], "text": w}
            for ts, w in zip(timestamp, text.split())
            if len(ts) >= 2
        ]

    if text:
        return [{"start": 0.0, "end": 0.0, "text": text}]

    return []


def transcribe(audio_path: str, output_path: str, language: str = None,
               device: str = "cpu", vad_config: dict = None):
    from funasr import AutoModel

    print(f"model: Fun-ASR-Nano", file=sys.stderr)
    print(f"device: {device}", file=sys.stderr)
    print(f"transcribing: {audio_path}", file=sys.stderr)

    kwargs = {
        "model": "FunAudioLLM/Fun-ASR-Nano-2512",
        "vad_model": "fsmn-vad",
        "punc_model": "ct-punc",
        "device": device,
    }
    if vad_config:
        kwargs["vad_kwargs"] = vad_config
    if language:
        kwargs["language"] = language

    model = AutoModel(**kwargs)
    result = model.generate(input=audio_path)

    segments = extract_segments(result)
    if not segments:
        print("error: no transcription segments extracted", file=sys.stderr)
        print(f"raw result: {json.dumps(result, default=str, ensure_ascii=False)[:500]}", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            f.write(f"{i}\n")
            f.write(f"{format_timestamp(seg['start'])} --> {format_timestamp(seg['end'])}\n")
            f.write(f"{seg['text']}\n\n")

    print(f"done! {len(segments)} segments -> {output_path}", file=sys.stderr)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio to SRT using FunASR")
    parser.add_argument("audio", help="audio file path (WAV 16kHz mono)")
    parser.add_argument("--output", "-o", help="output SRT path")
    parser.add_argument("--language", "-l", help="language code (default: auto-detect)")
    parser.add_argument("--device", "-d", default="cpu", choices=["cpu", "mps", "cuda"],
                        help="compute device (default: cpu)")
    parser.add_argument("--vad-config", type=json.loads, default=None,
                        help='VAD kwargs JSON, e.g. \'{"max_single_segment_time": 60000}\'')

    args = parser.parse_args()

    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"error: file not found {audio_path}", file=sys.stderr)
        sys.exit(1)

    output_path = args.output or str(audio_path.with_suffix(".srt"))

    transcribe(
        str(audio_path),
        output_path,
        language=args.language,
        device=args.device,
        vad_config=args.vad_config,
    )


if __name__ == "__main__":
    main()
