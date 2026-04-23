"""End-to-end pipeline orchestrator for the manhwa-mvp project (v0.2).

Reads `config.json`, runs each step in order, validates expected files, and
prints a final summary.

Steps (v0.2):
    1.  prep_inputs   -> storage/temp/scans_prepared/
    2.  anilist       -> storage/temp/anilist.json
    3.  wiki          -> storage/temp/wiki.json
    4.  community     -> storage/temp/community.json
    5.  ocr           -> storage/temp/ocr.json (run on scans_prepared)
    6.  context       -> storage/temp/context.json
    7.  scenes        -> storage/temp/scenes.json
    8.  qa            -> storage/temp/qa.json
    9.  tts           -> storage/output/narration.wav + scene_timeline.json
   10.  plan          -> storage/temp/scene_plan.json
   11.  transform     -> storage/temp/video_segments/seg_*.mp4
   12.  render        -> storage/output/final_video.mp4

Usage:
    python run_pipeline.py
    python run_pipeline.py --skip-step community
    python run_pipeline.py --only-step scenes
    python run_pipeline.py --config alt_config.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable

logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Slug utilities
# ---------------------------------------------------------------------------


def slugify(text: str) -> str:
    """Normalize a free-form title to a stable folder slug.

    Examples:
        "Solo Leveling"                    -> "solo_leveling"
        "The 3rd Prince of the Kingdom"    -> "the_3rd_prince_of_the_kingdom"
        "Tower of God: Récits"             -> "tower_of_god_recits"
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", " ", text).strip().lower()
    text = re.sub(r"[-\s]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "untitled"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


@dataclass
class Config:
    raw: dict
    title_override: str | None = None

    @classmethod
    def load(cls, path: Path, title_override: str | None = None) -> "Config":
        if not path.exists():
            raise FileNotFoundError(f"config file not found: {path}")
        return cls(
            raw=json.loads(path.read_text(encoding="utf-8")),
            title_override=title_override,
        )

    @property
    def title(self) -> str:
        return self.title_override or self.raw["title"]

    @property
    def slug(self) -> str:
        return slugify(self.title)

    @property
    def scans_dir(self) -> Path:
        """Title-specific scans directory: <scans_input>/<slug>."""
        return self.path("scans_input") / self.slug

    @property
    def scans_prepared_dir(self) -> Path:
        """Title-specific prepared-slices directory: <scans_prepared>/<slug>."""
        return self.path("scans_prepared") / self.slug

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
    logger.info("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"command failed (exit {result.returncode}): {' '.join(cmd)}")


def must_exist(path: Path, label: str) -> None:
    if not path.exists():
        raise FileNotFoundError(f"{label} not produced at {path}")
    logger.info("[ok] %s -> %s", label, path)


def ensure_directories(cfg: Config) -> None:
    for key in ("temp", "output", "scans_prepared"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)
    cfg.scans_prepared_dir.mkdir(parents=True, exist_ok=True)


def ensure_inputs_present(cfg: Config) -> None:
    """Accept either images OR PDFs in <scans_input>/<slug-of-title>.

    Convention: each manhwa has its own sub-folder named after the title slug,
    so the same `storage/input/scans` can host multiple titles without mixing
    pages from different works.
    """
    scans_root = cfg.path("scans_input")
    if not scans_root.exists():
        raise FileNotFoundError(f"scans root does not exist: {scans_root}")

    title_dir = cfg.scans_dir
    if not title_dir.exists():
        available = sorted(
            p.name for p in scans_root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )
        hint = (
            f"Available sub-folders under {scans_root}: {available}"
            if available
            else f"No sub-folders found under {scans_root}."
        )
        raise FileNotFoundError(
            f"No scans folder for title {cfg.title!r} (expected: {title_dir}). "
            f"{hint} "
            f"Either rename your folder to '{cfg.slug}' or override with "
            f"--title \"<exact title>\"."
        )

    accepted = []
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".pdf"):
        accepted.extend(title_dir.rglob(f"*{ext}"))
    if not accepted:
        def _has_scans(folder: Path) -> bool:
            for ext in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp"):
                if next(iter(folder.rglob(f"*{ext}")), None) is not None:
                    return True
            return False

        siblings_with_content = sorted(
            p.name for p in scans_root.iterdir()
            if p.is_dir() and p != title_dir and _has_scans(p)
        )
        hint = (
            f" Other sub-folders that contain scans: {siblings_with_content}."
            if siblings_with_content else ""
        )
        raise FileNotFoundError(
            f"no images or PDFs found under {title_dir}. "
            f"Add at least one .jpg/.png/.pdf file in this folder.{hint}"
        )
    logger.info("[ok] scans+pdf inputs: %d file(s) under %s", len(accepted), title_dir)


def check_external_tools() -> None:
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg not found in PATH.")
    if shutil.which("piper") is None:
        logger.warning("piper not found in PATH; the TTS step will fail.")


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def step_prep_inputs(cfg: Config) -> None:
    out = cfg.scans_prepared_dir
    cmd = [
        PYTHON, "-m", "scripts.prep_inputs",
        "--scans-dir", str(cfg.scans_dir),
        "--output-dir", str(out),
        "--target-width", str(cfg.get("pdf", "target_width", default=1280)),
        "--slice-height", str(cfg.get("pdf", "slice_height", default=720)),
        "--dpi", str(cfg.get("pdf", "extraction_dpi", default=150)),
        "--max-merged-height", str(cfg.get("pdf", "max_merged_height", default=50000)),
        "--max-pages-per-pdf", str(cfg.get("pdf", "max_pages_per_pdf", default=200)),
    ]
    if not cfg.get("pdf", "use_raw_extraction_when_possible", default=True):
        cmd.append("--no-raw-extract")
    run(cmd)
    if not any(out.iterdir()):
        raise RuntimeError(f"prep_inputs produced no files in {out}")
    logger.info("[ok] prepared scans in %s", out)


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
        logger.warning("community step failed softly: %s", exc)
    must_exist(out, "community.json")


def step_ocr(cfg: Config) -> None:
    out = cfg.path("temp") / "ocr.json"
    run([
        PYTHON, "-m", "scripts.ocr_scans",
        "--scans-dir", str(cfg.scans_prepared_dir),
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


def step_scenes(cfg: Config) -> None:
    out = cfg.path("temp") / "scenes.json"
    run([
        PYTHON, "-m", "scripts.generate_scenes_gemini",
        "--context", str(cfg.path("temp") / "context.json"),
        "--output", str(out),
        "--model", cfg.get("gemini", "narrative_model", default="gemini-2.5-pro"),
        "--min-scenes", str(cfg.get("scenes", "min_scenes", default=5)),
        "--max-scenes", str(cfg.get("scenes", "max_scenes", default=10)),
        "--default-duration-hint", str(cfg.get("scenes", "default_duration_hint_sec", default=8)),
        "--max-duration-hint", str(cfg.get("scenes", "max_duration_hint_sec", default=30)),
    ])
    must_exist(out, "scenes.json")


def step_qa(cfg: Config) -> None:
    out = cfg.path("temp") / "qa.json"
    cmd = [
        PYTHON, "-m", "scripts.qa_script",
        "--scenes", str(cfg.path("temp") / "scenes.json"),
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
        PYTHON, "-m", "scripts.tts_per_scene",
        "--scenes", str(cfg.path("temp") / "scenes.json"),
        "--piper-model", str(cfg.path("piper_model")),
        "--output", str(out),
        "--temp-dir", str(cfg.path("temp")),
        "--silence", str(cfg.get("tts", "silence_between_scenes_sec", default=0.6)),
    ])
    must_exist(out, "narration.wav")
    must_exist(cfg.path("temp") / "scene_timeline.json", "scene_timeline.json")


def step_plan(cfg: Config) -> None:
    out = cfg.path("temp") / "scene_plan.json"
    run([
        PYTHON, "-m", "scripts.plan_video",
        "--scenes", str(cfg.path("temp") / "scenes.json"),
        "--timeline", str(cfg.path("temp") / "scene_timeline.json"),
        "--scans-prepared", str(cfg.scans_prepared_dir),
        "--output", str(out),
        "--width", str(cfg.get("video", "width", default=1280)),
        "--height", str(cfg.get("video", "height", default=720)),
        "--fps", str(cfg.get("video", "fps", default=30)),
        "--min-slice", str(cfg.get("video", "min_slice_duration_sec", default=1.0)),
        "--max-slice", str(cfg.get("video", "max_slice_duration_sec", default=8.0)),
    ])
    must_exist(out, "scene_plan.json")


def step_transform(cfg: Config) -> None:
    seg_dir = cfg.path("temp") / "video_segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    run([
        PYTHON, "-m", "scripts.transform_images",
        "--plan", str(cfg.path("temp") / "scene_plan.json"),
        "--out-dir", str(seg_dir),
    ])
    if not list(seg_dir.glob("seg_*.mp4")):
        raise RuntimeError(f"no video segments produced under {seg_dir}")
    logger.info("[ok] video segments produced under %s", seg_dir)


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
    "prep_inputs": step_prep_inputs,
    "anilist": step_anilist,
    "wiki": step_wiki,
    "community": step_community,
    "ocr": step_ocr,
    "context": step_context,
    "scenes": step_scenes,
    "qa": step_qa,
    "tts": step_tts,
    "plan": step_plan,
    "transform": step_transform,
    "render": step_render,
}

CRITICAL_STEPS = {"prep_inputs", "ocr", "context", "scenes", "tts", "plan", "transform", "render"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manhwa-MVP pipeline orchestrator (v0.2).")
    parser.add_argument("--config", default="config.json", type=Path)
    parser.add_argument(
        "--title",
        default=None,
        help="Override config.title for this run; the scans subfolder is "
             "auto-resolved from a slug of the title.",
    )
    parser.add_argument(
        "--skip-step", action="append", default=[], choices=list(STEPS.keys()),
    )
    parser.add_argument(
        "--only-step", action="append", default=[], choices=list(STEPS.keys()),
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] [%(name)s] %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    load_dotenv()

    cfg = Config.load(ROOT / args.config, title_override=args.title)
    if args.title and re.fullmatch(r"[a-z0-9_]+", args.title):
        logger.warning(
            "--title %r looks like a slug, not a human title. Pass the real "
            "title (e.g. \"The 3rd Prince of the Fallen Kingdom Returns\") so "
            "external sources (AniList, Wikipedia) can find it.",
            args.title,
        )
    logger.info("title=%r  slug=%r  scans=%s", cfg.title, cfg.slug, cfg.scans_dir)
    ensure_directories(cfg)
    ensure_inputs_present(cfg)
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
            if name in CRITICAL_STEPS:
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
