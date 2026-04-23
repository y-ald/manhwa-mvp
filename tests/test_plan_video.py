"""Tests for scripts.plan_video distribution logic."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.plan_video import build_plan, distribute  # noqa: E402


def make_slices(n: int) -> list[Path]:
    return [Path(f"slice_{i:03d}.png") for i in range(1, n + 1)]


def test_distribute_equal_durations_even_split() -> None:
    durations = [10.0, 10.0, 10.0]  # 3 scenes
    slices = make_slices(6)
    groups = distribute(durations, slices)
    assert [len(g) for g in groups] == [2, 2, 2]


def test_distribute_proportional() -> None:
    durations = [5.0, 15.0, 5.0]  # middle scene has 60% of total
    slices = make_slices(10)
    groups = distribute(durations, slices)
    assert sum(len(g) for g in groups) == 10
    # middle scene should have the most
    assert len(groups[1]) >= len(groups[0])
    assert len(groups[1]) >= len(groups[2])


def test_distribute_more_scenes_than_slices_cycles() -> None:
    durations = [5.0, 5.0, 5.0, 5.0, 5.0]  # 5 scenes
    slices = make_slices(2)
    groups = distribute(durations, slices)
    assert len(groups) == 5
    assert all(len(g) == 1 for g in groups)
    # cycles through slices
    assert groups[0][0] == slices[0]
    assert groups[1][0] == slices[1]
    assert groups[2][0] == slices[0]


def test_distribute_each_scene_gets_at_least_one_slice() -> None:
    durations = [100.0, 0.5, 0.5]  # very imbalanced
    slices = make_slices(3)
    groups = distribute(durations, slices)
    assert all(len(g) >= 1 for g in groups)
    assert sum(len(g) for g in groups) == 3


def test_build_plan_emits_clamped_durations() -> None:
    scenes = {
        "scenes": [
            {"id": 1, "type": "hook", "tone": "mystérieux", "duration_hint_sec": 8},
            {"id": 2, "type": "ending", "tone": "sombre", "duration_hint_sec": 12},
        ]
    }
    timeline = {
        "scenes": [
            {"id": 1, "duration_sec": 8.0},
            {"id": 2, "duration_sec": 100.0},  # forces clamp on per-slice
        ]
    }
    slices = make_slices(2)
    plan = build_plan(
        scenes, timeline, slices,
        width=1280, height=720, fps=30,
        min_slice=1.0, max_slice=8.0,
    )
    assert plan["video"]["width"] == 1280
    assert len(plan["scenes"]) == 2
    # Scene 2 has 100s for 1 slice -> clamped to max_slice=8.0
    scene2 = plan["scenes"][1]
    for sl in scene2["slices"]:
        assert 1.0 <= sl["duration_sec"] <= 8.0
