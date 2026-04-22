"""End-to-end pipeline orchestrator for the manhwa-mvp project.

Reads `config.json`, runs each step in order, validates expected files, and
prints a final summary.

Each step is implemented as a small function so any of them can be re-run
in isolation (`--only-step ...`) or skipped (`--skip-step ...`).

Steps:
    1. anilist        -> storage/temp/anilist.json
    2. wiki           -> storage/temp/wiki.json
    3. community      -> storage/temp/community.json
    4. ocr            -> storage/temp/ocr.json
    5. context        -> storage/temp/context.json
    6. gemini         -> storage/temp/script.txt
    7. qa             -> storage/temp/qa.json
    8. tts            -> storage/output/narration.wav
    9. transform      -> storage/temp/video_segments/seg_*.mp4
   10. render         -> storage/output/final_video.mp4

Usage:
    python run_pipeline.py
    python run_pipeline.py --skip-step community
    python run_pipeline.py --only-step gemini
    python run_pipeline.py --config alt_config.json
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


@dataclass
class Config:
    raw: dict

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not path.exists():
            raise FileNotFoundError(f"config file not found: {path}")
        return cls(raw=json.loads(path.read_text(encoding="utf-8")))

    @property
    def title(self) -> str:
        return self.raw["title"]

    def path(self, key: str) -> Path:
        return (ROOT / self.raw["paths"][key]).resolve()

    def get(self, *keys, default=None):
        node = self.raw
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(cmd: list[str]) -> None:
    """Run a subprocess and stream output. Raise on non-zero exit."""
    logger.info("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"command failed (exit {result.returncode}): {' '.join(cmd)}")


def must_exist(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not produced at {path}")
    logger.info("✓ %s -> %s", label, path)


def ensure_directories(cfg: Config) -> None:
    for key in ("temp", "output"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)


def ensure_scans_present(cfg: Config) -> None:
    scans_root = cfg.path("scans_input")
    if not scans_root.exists():
        raise FileNotFoundError(f"scans dir does not exist: {scans_root}")
    images = []
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        images.extend(scans_root.rglob(f"*{ext}"))
    if not images:
        raise FileNotFoundError(
            f"no images found under {scans_root}. "
            f"Add at least one .jpg/.png file in a sub-folder."
        )
    logger.info("✓ scans found: %d image(s) under %s", len(images), scans_root)


def check_external_tools() -> None:
    missing = [t for t in ("ffmpeg",) if shutil.which(t) is None]
    if missing:
        raise RuntimeError(
            f"missing external tool(s): {', '.join(missing)}. "
            f"Install them before running the pipeline."
        )
    if shutil.which("piper") is None:
        logger.warning("piper not found in PATH; the TTS step will fail.")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_anilist(cfg: Config) -> None:
    out = cfg.path("temp") / "anilist.json"
    run([PYTHON, "-m", "scripts.scrape_anilist", "--title", cfg.title, "--output", str(out)])
    must_exist(out, "anilist.json")


def step_wiki(cfg: Config) -> None:
    out = cfg.path("temp") / "wiki.json"
    run([
        PYTHON, "-m", "scripts.scrape_wiki",
        "--title", cfg.title,
        "--output", str(out),
        "--max-paragraphs", str(cfg.get("limits", "max_wiki_paragraphs", default=10)),
        "--max-bullets", str(cfg.get("limits", "max_wiki_bullets", default=20)),
    ])
    must_exist(out, "wiki.json")


def step_community(cfg: Config) -> None:
    out = cfg.path("temp") / "community.json"
    subs = cfg.get("reddit", "subreddits", default=["manhwa", "manga"]) or []
    cmd = [
        PYTHON, "-m", "scripts.scrape_community",
        "--title", cfg.title,
        "--output", str(out),
        "--subreddits", *subs,
        "--max-posts-per-sub", str(cfg.get("reddit", "max_posts_per_sub", default=8)),
        "--max-comments-per-post", str(cfg.get("reddit", "max_comments_per_post", default=5)),
        "--max-snippets", str(cfg.get("limits", "max_community_snippets", default=10)),
    ]
    try:
        run(cmd)
    except RuntimeError as exc:
        # Reddit creds may be missing; the script has already written an empty file.
        logger.warning("community step failed softly: %s", exc)
    must_exist(out, "community.json")


def step_ocr(cfg: Config) -> None:
    out = cfg.path("temp") / "ocr.json"
    run([
        PYTHON, "-m", "scripts.ocr_scans",
        "--scans-dir", str(cfg.path("scans_input")),
        "--output", str(out),
        "--lang", cfg.get("ocr", "lang", default="en"),
        "--max-lines", str(cfg.get("limits", "max_ocr_lines", default=100)),
        "--max-cues", str(cfg.get("limits", "max_emotional_cues", default=50)),
    ])
    must_exist(out, "ocr.json")


def step_context(cfg: Config) -> None:
    out = cfg.path("temp") / "context.json"
    run([
        PYTHON, "-m", "scripts.build_context",
        "--temp-dir", str(cfg.path("temp")),
        "--output", str(out),
    ])
    must_exist(out, "context.json")


def step_gemini(cfg: Config) -> None:
    out = cfg.path("temp") / "script.txt"
    run([
        PYTHON, "-m", "scripts.generate_script_gemini",
        "--context", str(cfg.path("temp") / "context.json"),
        "--output", str(out),
        "--model", cfg.get("gemini", "narrative_model", default="gemini-2.5-pro"),
    ])
    must_exist(out, "script.txt")


def step_qa(cfg: Config) -> None:
    out = cfg.path("temp") / "qa.json"
    cmd = [
        PYTHON, "-m", "scripts.qa_script",
        "--script", str(cfg.path("temp") / "script.txt"),
        "--ocr", str(cfg.path("temp") / "ocr.json"),
        "--output", str(out),
        "--llm-model", cfg.get("gemini", "qa_model", default="gemini-2.5-flash"),
    ]
    if cfg.get("gemini", "use_llm_qa", default=False):
        cmd.append("--use-llm")
    run(cmd)
    must_exist(out, "qa.json")


def step_tts(cfg: Config) -> None:
    out = cfg.path("output") / "narration.wav"
    run([
        "bash", "scripts/tts_piper.sh",
        str(cfg.path("temp") / "script.txt"),
        str(cfg.path("piper_model")),
        str(out),
    ])
    must_exist(out, "narration.wav")


def step_transform(cfg: Config) -> None:
    seg_dir = cfg.path("temp") / "video_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    run([
        "bash", "scripts/transform_images.sh",
        str(cfg.path("scans_input")),
        str(seg_dir),
        str(cfg.get("video", "width", default=1280)),
        str(cfg.get("video", "height", default=720)),
        str(cfg.get("video", "fps", default=30)),
        str(cfg.get("video", "segment_duration", default=3)),
    ])
    if not list(seg_dir.glob("seg_*.mp4")):
        raise RuntimeError(f"no video segments produced under {seg_dir}")
    logger.info("✓ video segments produced under %s", seg_dir)


def step_render(cfg: Config) -> None:
    out = cfg.path("output") / "final_video.mp4"
    run([
        "bash", "scripts/render_video.sh",
        str(cfg.path("temp") / "video_segments"),
        str(cfg.path("output") / "narration.wav"),
        str(out),
    ])
    must_exist(out, "final_video.mp4")


STEPS: dict[str, Callable[[Config], None]] = {
    "anilist": step_anilist,
    "wiki": step_wiki,
    "community": step_community,
    "ocr": step_ocr,
    "context": step_context,
    "gemini": step_gemini,
    "qa": step_qa,
    "tts": step_tts,
    "transform": step_transform,
    "render": step_render,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manhwa-MVP pipeline orchestrator.")
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument(
        "--skip-step", action="append", default=[], choices=list(STEPS.keys()),
        help="Skip one or more steps (can be repeated).",
    )
    parser.add_argument(
        "--only-step", action="append", default=[], choices=list(STEPS.keys()),
        help="Run only these step(s) (can be repeated).",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv()

    cfg = Config.load(ROOT / args.config)
    ensure_directories(cfg)
    ensure_scans_present(cfg)
    check_external_tools()

    if args.only_step:
        plan = [s for s in STEPS if s in args.only_step]
    else:
        plan = [s for s in STEPS if s not in args.skip_step]

    logger.info("=== Pipeline plan: %s", " -> ".join(plan))
    failed: list[str] = []

    for name in plan:
        logger.info("--- step: %s", name)
        try:
            STEPS[name](cfg)
        except Exception as exc:  # noqa: BLE001
            logger.error("step %s failed: %s", name, exc)
            failed.append(name)
            # Hard stop on critical steps; others are tolerated.
            if name in {"anilist", "ocr", "context", "gemini", "tts", "transform", "render"}:
                logger.error("critical step %s failed, aborting.", name)
                break

    logger.info("================ summary ================")
    logger.info("title:     %s", cfg.title)
    logger.info("planned:   %s", plan)
    logger.info("failed:    %s", failed or "none")
    logger.info("output:    %s", cfg.path("output"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
