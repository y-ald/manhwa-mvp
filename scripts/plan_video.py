"""Build a `scene_plan.json` mapping prepared image slices to scenes.

Inputs:
  - scenes.json          (Gemini structured output)
  - scene_timeline.json  (real audio durations per scene)
  - scans_prepared dir   (flat dir of normalized .png/.jpg slices)

Strategy (v0.2 = naïve sequential proportional):
  - Distribute slices in their natural order across scenes proportionally to
    each scene's audio duration.
  - Each scene gets at least 1 slice (cycles back if scenes > slices).
  - Per-slice display duration is constrained by config min/max.

Output:
  scene_plan.json
  {
    "video": { "width": 1280, "height": 720, "fps": 30 },
    "scenes": [
      {
        "id": 1,
        "tone": "mystérieux",
        "duration_sec": 8.2,
        "slices": [
          { "path": "...", "duration_sec": 4.1 },
          { "path": "...", "duration_sec": 4.1 }
        ]
      }
    ]
  }

Usage:
    python -m scripts.plan_video \
        --scenes storage/temp/scenes.json \
        --timeline storage/temp/scene_timeline.json \
        --scans-prepared storage/temp/scans_prepared \
        --output storage/temp/scene_plan.json \
        --width 1280 --height 720 --fps 30 \
        --min-slice 1.0 --max-slice 8.0
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger("plan")

VALID_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def list_slices(scans_prepared: Path) -> list[Path]:
    from natsort import natsorted

    files: list[Path] = []
    for p in scans_prepared.iterdir() if scans_prepared.exists() else []:
        if p.is_file() and p.suffix.lower() in VALID_EXTS:
            files.append(p)
    return natsorted(files)


def distribute(
    scene_durations: list[float],
    slices: list[Path],
) -> list[list[Path]]:
    """Return a list of slice-lists, one per scene, total slices preserved.

    If slices < scenes, cycle through slices so every scene gets at least one.
    If slices >= scenes, distribute proportionally to scene_durations.
    """
    n_scenes = len(scene_durations)
    n_slices = len(slices)
    if n_scenes == 0 or n_slices == 0:
        return [[] for _ in range(n_scenes)]

    if n_slices < n_scenes:
        out: list[list[Path]] = []
        for i in range(n_scenes):
            out.append([slices[i % n_slices]])
        return out

    total_dur = sum(scene_durations) or 1.0
    # Floor allocation, then distribute remainder by largest fractional parts.
    raw = [n_slices * (d / total_dur) for d in scene_durations]
    floor = [int(x) for x in raw]
    # Ensure each scene gets at least 1.
    for i in range(n_scenes):
        if floor[i] == 0:
            floor[i] = 1
    used = sum(floor)
    remaining = n_slices - used
    if remaining > 0:
        # add to scenes with largest fractional parts
        order = sorted(range(n_scenes), key=lambda i: raw[i] - int(raw[i]), reverse=True)
        for i in order[:remaining]:
            floor[i] += 1
    elif remaining < 0:
        # need to remove; remove from scenes with > 1 slice, smallest fractional first
        order = sorted(range(n_scenes), key=lambda i: raw[i] - int(raw[i]))
        for i in order:
            while remaining < 0 and floor[i] > 1:
                floor[i] -= 1
                remaining += 1
            if remaining == 0:
                break

    # Build the final list by walking slices in order.
    result: list[list[Path]] = []
    cursor = 0
    for k in floor:
        result.append(slices[cursor : cursor + k])
        cursor += k
    return result


def build_plan(
    scenes_json: dict,
    timeline_json: dict,
    slices: list[Path],
    width: int,
    height: int,
    fps: int,
    min_slice: float,
    max_slice: float,
) -> dict:
    scenes = scenes_json.get("scenes") or []
    timeline_scenes = {s["id"]: s for s in (timeline_json.get("scenes") or [])}

    durations: list[float] = []
    for s in scenes:
        tl = timeline_scenes.get(s.get("id"))
        if tl and tl.get("duration_sec"):
            durations.append(float(tl["duration_sec"]))
        else:
            # Scene was probably skipped during TTS; fall back to hint.
            durations.append(float(s.get("duration_hint_sec") or 5))

    grouped = distribute(durations, slices)

    plan_scenes = []
    for scene, dur, group in zip(scenes, durations, grouped):
        if not group:
            continue
        per_slice = dur / len(group)
        per_slice = max(min_slice, min(per_slice, max_slice))
        plan_scenes.append(
            {
                "id": scene.get("id"),
                "type": scene.get("type"),
                "tone": scene.get("tone", "neutre"),
                "duration_sec": round(dur, 3),
                "slices": [
                    {"path": str(p), "duration_sec": round(per_slice, 3)}
                    for p in group
                ],
            }
        )

    return {
        "video": {"width": width, "height": height, "fps": fps},
        "scenes": plan_scenes,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build scene_plan.json.")
    parser.add_argument("--scenes", required=True, type=Path)
    parser.add_argument("--timeline", required=True, type=Path)
    parser.add_argument("--scans-prepared", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--min-slice", type=float, default=1.0)
    parser.add_argument("--max-slice", type=float, default=8.0)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    scenes_json = json.loads(args.scenes.read_text(encoding="utf-8"))
    timeline_json = json.loads(args.timeline.read_text(encoding="utf-8"))
    slices = list_slices(args.scans_prepared)
    if not slices:
        logger.error("no prepared slices found in %s", args.scans_prepared)
        return 1

    plan = build_plan(
        scenes_json,
        timeline_json,
        slices,
        args.width,
        args.height,
        args.fps,
        args.min_slice,
        args.max_slice,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    n_scenes = len(plan["scenes"])
    n_slices = sum(len(s["slices"]) for s in plan["scenes"])
    logger.info("Wrote %s (%d scene(s), %d slice slot(s))", args.output, n_scenes, n_slices)
    return 0


if __name__ == "__main__":
    sys.exit(main())
