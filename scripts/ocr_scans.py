"""Run PaddleOCR on a folder of scans and emit a JSON file.

Output schema:
    {
      "ocr_text_sample": [...],      # raw lines, capped
      "emotional_cues": [...]         # short / punctuation-heavy lines
    }

Usage:
    python -m scripts.ocr_scans --scans-dir storage/input/scans/solo_leveling \
        --output storage/temp/ocr.json --lang en
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger("ocr")

IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

# Heuristic: cues are short, punctuated, often onomatopoeia or emotional outbursts.
EMO_PUNCT_RE = re.compile(r"[!?…]{1,}|\.\.\.")


def list_images(scans_dir: Path) -> list[Path]:
    if not scans_dir.exists():
        return []
    images: list[Path] = []
    for ext in IMG_EXTS:
        images.extend(scans_dir.rglob(f"*{ext}"))
    return sorted(images)


def is_emotional(line: str) -> bool:
    if not line:
        return False
    line_stripped = line.strip()
    if len(line_stripped) <= 30 and EMO_PUNCT_RE.search(line_stripped):
        return True
    if len(line_stripped) <= 12:  # short utterances tend to be cries / SFX
        return True
    upper_ratio = sum(1 for c in line_stripped if c.isupper()) / max(len(line_stripped), 1)
    return upper_ratio > 0.6 and len(line_stripped) <= 40


def run_ocr(
    images: list[Path], lang: str, max_lines: int, max_cues: int
) -> dict[str, list[str]]:
    # Lazy import: paddleocr is heavy and only needed here.
    from paddleocr import PaddleOCR

    logger.info("Loading PaddleOCR model lang=%s", lang)
    # PaddleOCR v3.x API: `use_angle_cls`/`show_log` removed; orientation flag
    # renamed to `use_textline_orientation`.
    ocr = PaddleOCR(use_textline_orientation=True, lang=lang)

    sample: list[str] = []
    cues: list[str] = []

    for idx, img_path in enumerate(images, start=1):
        logger.info("OCR %d/%d %s", idx, len(images), img_path.name)
        try:
            # `predict()` is the v3.x entry point; returns a list of OCRResult
            # objects (one per input image) exposing dict-like access.
            results = ocr.predict(str(img_path))
        except Exception as exc:  # noqa: BLE001
            logger.warning("OCR failed for %s: %s", img_path, exc)
            continue

        if not results:
            continue

        # Flatten texts (and scores, kept here for future thresholding) across
        # every page result returned by PaddleOCR.
        texts: list[str] = []
        for page in results:
            try:
                texts.extend(page.get("rec_texts") or [])
            except (AttributeError, TypeError):
                continue

        for raw in texts:
            text = (raw or "").strip()
            if not text:
                continue
            if len(sample) < max_lines:
                sample.append(text)
            if is_emotional(text) and len(cues) < max_cues:
                cues.append(text)
            if len(sample) >= max_lines and len(cues) >= max_cues:
                break

    return {"ocr_text_sample": sample, "emotional_cues": cues}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OCR a folder of scans.")
    parser.add_argument("--scans-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--lang", default="en", choices=["en", "french", "korean"])
    parser.add_argument("--max-lines", type=int, default=100)
    parser.add_argument("--max-cues", type=int, default=50)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")

    images = list_images(args.scans_dir)
    if not images:
        logger.error("No images found in %s", args.scans_dir)
        # Write empty so the pipeline can decide to continue or stop.
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps({"ocr_text_sample": [], "emotional_cues": []}, indent=2),
            encoding="utf-8",
        )
        return 1

    data = run_ocr(images, args.lang, args.max_lines, args.max_cues)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Wrote %s (%d sample lines, %d cues)",
        args.output,
        len(data["ocr_text_sample"]),
        len(data["emotional_cues"]),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
