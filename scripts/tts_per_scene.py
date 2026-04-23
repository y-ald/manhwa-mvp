"""Per-scene TTS via Piper, then concatenate into a single narration.wav with
silences between scenes and emit a precise timeline file.

Pipeline per scene:
    text -> piper -> scene_NNN.wav -> ffprobe duration -> insert into timeline

Then:
    [scene_001.wav, silence.wav, scene_002.wav, silence.wav, ...]
        -> ffmpeg concat -> narration.wav

Outputs:
    <output_dir>/narration.wav
    <temp_dir>/scene_audio/scene_NNN.wav
    <temp_dir>/scene_timeline.json

scene_timeline.json schema:
    {
      "total_duration_sec": 287.4,
      "silence_between_sec": 0.6,
      "scenes": [
        { "id": 1, "wav": "scene_001.wav", "start_sec": 0.0, "duration_sec": 8.2 },
        ...
      ]
    }

Usage:
    python -m scripts.tts_per_scene \
        --scenes storage/temp/scenes.json \
        --piper-model storage/models/fr_FR-siwis-medium.onnx \
        --output storage/output/narration.wav \
        --temp-dir storage/temp \
        --silence 0.6
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path

logger = logging.getLogger("tts")


def piper_synthesize(text: str, model: Path, out_wav: Path) -> None:
    """Pipe text into the Piper CLI binary and write a wav."""
    if shutil.which("piper") is None:
        raise RuntimeError("piper binary not found in PATH.")
    if not model.exists():
        raise FileNotFoundError(f"Piper model not found: {model}")
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["piper", "--model", str(model), "--output_file", str(out_wav)],
        input=text.encode("utf-8"),
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"piper failed (exit {proc.returncode}): {proc.stderr.decode('utf-8', 'ignore')}"
        )
    if not out_wav.exists() or out_wav.stat().st_size == 0:
        raise RuntimeError(f"piper produced empty wav: {out_wav}")


def wav_duration_sec(path: Path) -> float:
    """Read wav duration without depending on ffprobe (safer)."""
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        rate = w.getframerate()
        if rate == 0:
            return 0.0
        return frames / float(rate)


def make_silence(path: Path, duration_sec: float, sample_rate: int = 22050) -> None:
    """Generate a tiny silent wav with the right sample rate (Piper output rate)."""
    n_frames = int(round(duration_sec * sample_rate))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit PCM
        w.setframerate(sample_rate)
        w.writeframes(b"\x00\x00" * n_frames)


def concat_wavs(parts: list[Path], output: Path) -> None:
    """Use ffmpeg concat demuxer to merge wav parts losslessly when possible."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    list_file = output.parent / "_concat_list.txt"
    with list_file.open("w", encoding="utf-8") as f:
        for p in parts:
            abs_p = p.resolve()
            f.write(f"file '{abs_p}'\n")
    output.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(output),
            "-loglevel", "error",
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        # Fallback: re-encode (handles sample-rate mismatches between scenes).
        logger.warning("concat copy failed, re-encoding...")
        proc = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-ar", "22050", "-ac", "1",
                str(output),
                "-loglevel", "error",
            ],
            capture_output=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed: {proc.stderr.decode('utf-8', 'ignore')}"
            )
    list_file.unlink(missing_ok=True)


def run(
    scenes_path: Path,
    piper_model: Path,
    output_wav: Path,
    temp_dir: Path,
    silence_sec: float,
) -> dict:
    payload = json.loads(scenes_path.read_text(encoding="utf-8"))
    scenes = payload.get("scenes") or []
    if not scenes:
        raise RuntimeError("no scenes in input file.")

    audio_dir = temp_dir / "scene_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    timeline_scenes: list[dict] = []
    parts: list[Path] = []

    silence_wav = audio_dir / "_silence.wav"
    make_silence(silence_wav, silence_sec)

    cursor = 0.0
    for i, scene in enumerate(scenes, start=1):
        text = (scene.get("text") or "").strip()
        if not text:
            logger.warning("scene %d empty, skipping.", i)
            continue
        wav_name = f"scene_{i:03d}.wav"
        wav_path = audio_dir / wav_name
        try:
            piper_synthesize(text, piper_model, wav_path)
        except Exception as exc:  # noqa: BLE001
            logger.error("scene %d TTS failed: %s", i, exc)
            continue
        dur = wav_duration_sec(wav_path)
        timeline_scenes.append(
            {
                "id": scene.get("id", i),
                "wav": wav_name,
                "start_sec": round(cursor, 3),
                "duration_sec": round(dur, 3),
            }
        )
        parts.append(wav_path)
        cursor += dur
        if i < len(scenes):
            parts.append(silence_wav)
            cursor += silence_sec

    if not parts:
        raise RuntimeError("no scene produced audio; aborting.")

    concat_wavs(parts, output_wav)

    timeline = {
        "total_duration_sec": round(cursor, 3),
        "silence_between_sec": silence_sec,
        "scenes": timeline_scenes,
    }
    timeline_path = temp_dir / "scene_timeline.json"
    timeline_path.write_text(
        json.dumps(timeline, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "Wrote %s (total %.2fs across %d scene(s))",
        output_wav,
        timeline["total_duration_sec"],
        len(timeline_scenes),
    )
    logger.info("Wrote %s", timeline_path)
    return timeline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-scene Piper TTS + timeline.")
    parser.add_argument("--scenes", required=True, type=Path)
    parser.add_argument("--piper-model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--temp-dir", required=True, type=Path)
    parser.add_argument("--silence", type=float, default=0.6)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    try:
        run(args.scenes, args.piper_model, args.output, args.temp_dir, args.silence)
    except Exception as exc:  # noqa: BLE001
        logger.error("tts_per_scene failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
