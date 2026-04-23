"""Render one mp4 segment per slice from a `scene_plan.json`, applying a
mood-aware FFmpeg filter preset based on the scene `tone`.

Output:
  <out_dir>/seg_0001.mp4, seg_0002.mp4, ...   (in scene_plan order)

Usage:
    python -m scripts.transform_images \
        --plan storage/temp/scene_plan.json \
        --out-dir storage/temp/video_segments
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger("transform")


# Mood -> filter parameters. Tweak here to adjust the look-and-feel.
TONE_PRESETS: dict[str, dict] = {
    "mystérieux": {"zoom_step": 0.0008, "zoom_max": 1.12, "noise": 6, "vignette": "PI/5", "contrast": 1.05, "sat": 0.85},
    "sombre":     {"zoom_step": 0.0006, "zoom_max": 1.10, "noise": 8, "vignette": "PI/4", "contrast": 1.10, "sat": 0.70},
    "action":     {"zoom_step": 0.0020, "zoom_max": 1.20, "noise": 10, "vignette": "PI/6", "contrast": 1.15, "sat": 1.05},
    "émotion":    {"zoom_step": 0.0005, "zoom_max": 1.08, "noise": 0, "vignette": "PI/5", "contrast": 1.05, "sat": 0.90},
    "neutre":     {"zoom_step": 0.0010, "zoom_max": 1.15, "noise": 6, "vignette": "PI/5", "contrast": 1.10, "sat": 1.00},
}


def build_filter(width: int, height: int, fps: int, duration_sec: float, tone: str) -> str:
    preset = TONE_PRESETS.get(tone, TONE_PRESETS["neutre"])
    total_frames = max(1, int(round(duration_sec * fps)))
    noise_part = (
        f"noise=alls={preset['noise']}:allf=t," if preset["noise"] > 0 else ""
    )
    return (
        f"scale={width}*1.4:{height}*1.4:force_original_aspect_ratio=increase,"
        f"crop={width}*1.2:{height}*1.2,"
        f"zoompan=z='min(zoom+{preset['zoom_step']},{preset['zoom_max']})':"
        f"d={total_frames}:s={width}x{height}:fps={fps},"
        f"eq=contrast={preset['contrast']}:saturation={preset['sat']},"
        f"{noise_part}"
        f"vignette={preset['vignette']},"
        f"format=yuv420p"
    )


def render_segment(
    img_path: Path,
    out_path: Path,
    width: int,
    height: int,
    fps: int,
    duration_sec: float,
    tone: str,
) -> None:
    if not img_path.exists():
        raise FileNotFoundError(f"slice missing: {img_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    vf = build_filter(width, height, fps, duration_sec, tone)
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(img_path),
        "-vf", vf,
        "-t", f"{duration_sec:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-movflags", "+faststart",
        str(out_path),
        "-loglevel", "error",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed for {img_path.name}: {proc.stderr.decode('utf-8', 'ignore')}"
        )


def run(plan_path: Path, out_dir: Path) -> int:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    video = plan.get("video") or {}
    width = int(video.get("width", 1280))
    height = int(video.get("height", 720))
    fps = int(video.get("fps", 30))
    scenes = plan.get("scenes") or []
    if not scenes:
        raise RuntimeError("no scenes in plan.")

    out_dir.mkdir(parents=True, exist_ok=True)
    # clean previous segments
    for old in out_dir.glob("seg_*.mp4"):
        old.unlink()

    seg_idx = 0
    for scene in scenes:
        tone = scene.get("tone", "neutre")
        for sl in scene.get("slices", []):
            seg_idx += 1
            img = Path(sl["path"])
            dur = float(sl["duration_sec"])
            out = out_dir / f"seg_{seg_idx:04d}.mp4"
            logger.info(
                "[seg %04d] tone=%s dur=%.2fs src=%s",
                seg_idx, tone, dur, img.name,
            )
            render_segment(img, out, width, height, fps, dur, tone)

    if seg_idx == 0:
        raise RuntimeError("no segments produced.")
    logger.info("Done: %d segment(s) in %s", seg_idx, out_dir)
    return seg_idx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render mp4 segments from scene_plan.json.")
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    try:
        run(args.plan, args.out_dir)
    except Exception as exc:  # noqa: BLE001
        logger.error("transform_images failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
