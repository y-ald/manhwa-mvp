"""QA over the structured scenes.json (v0.2).

Modes:
- Local heuristics (default): flat-text checks (OCR overlap, dialogue markers,
  scene length, total word count).
- LLM second pass (--use-llm): defers to Gemini for a richer review.

Output (qa.json):
    {
      "risk_score": int,
      "warnings": [str, ...],
      "recommendations": [str, ...],
      "mode": "local" | "llm",
      "stats": { "n_scenes": int, "n_words": int, "n_chars": int }
    }

Usage:
    python -m scripts.qa_script --scenes storage/temp/scenes.json \
        --ocr storage/temp/ocr.json --output storage/temp/qa.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("qa")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

DIALOGUE_RE = re.compile(r'(["«»“”])([^"«»“”]{5,})\1|^[\-—]\s+\w', re.MULTILINE)


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def flatten_scenes(scenes_payload: dict) -> tuple[str, list[str]]:
    """Return (full_text, [per-scene texts])."""
    scenes = scenes_payload.get("scenes") or []
    parts = [(s.get("text") or "").strip() for s in scenes if (s.get("text") or "").strip()]
    return "\n\n".join(parts), parts


def local_qa(scenes_payload: dict, ocr_lines: list[str]) -> dict:
    full_text, scene_texts = flatten_scenes(scenes_payload)
    warnings: list[str] = []
    recommendations: list[str] = []
    score = 0

    norm_text = normalize(full_text)

    # 1) OCR overlap
    overlap_hits = 0
    for line in ocr_lines:
        n = normalize(line)
        if len(n) >= 12 and n in norm_text:
            overlap_hits += 1
            warnings.append(f"OCR phrase potentially copied verbatim: {line!r}")
    if overlap_hits:
        score += min(40, 8 * overlap_hits)
        recommendations.append("Reformuler les passages issus de l'OCR.")

    # 2) Dialogue markers
    dialogue_matches = DIALOGUE_RE.findall(full_text)
    if dialogue_matches:
        score += min(25, 5 * len(dialogue_matches))
        warnings.append(f"{len(dialogue_matches)} dialogue marker(s) detected.")
        recommendations.append("Remplacer les dialogues par du discours indirect.")

    # 3) Word count guard rails (target 600-900)
    word_count = len(full_text.split())
    if word_count < 500:
        score += 12
        warnings.append(f"Script too short ({word_count} words, target 600-900).")
    elif word_count > 1100:
        score += 6
        warnings.append(f"Script too long ({word_count} words, target 600-900).")

    # 4) Scene structure sanity
    n_scenes = len(scene_texts)
    if n_scenes < 4:
        score += 10
        warnings.append(f"Only {n_scenes} scene(s); narrative likely flat.")
    if any(len(t.split()) < 25 for t in scene_texts):
        score += 5
        recommendations.append("Étoffer les scènes les plus courtes.")
    if any(len(t.split()) > 250 for t in scene_texts):
        score += 5
        recommendations.append("Découper les scènes les plus longues.")

    # 5) Validate that scenes.json declared known tones / types
    scenes = scenes_payload.get("scenes") or []
    bad_tones = [s for s in scenes if s.get("tone") == "neutre"]
    if len(bad_tones) == len(scenes) and scenes:
        score += 8
        warnings.append("All scenes are 'neutre' tone; visual variety will be flat.")

    return {
        "risk_score": min(score, 100),
        "warnings": warnings,
        "recommendations": recommendations,
        "mode": "local",
        "stats": {
            "n_scenes": n_scenes,
            "n_words": word_count,
            "n_chars": len(full_text),
        },
    }


def llm_qa(scenes_payload: dict, ocr_lines: list[str], model: str) -> dict:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set; cannot use --use-llm.")

    full_text, _ = flatten_scenes(scenes_payload)
    prompt_tpl = (PROMPTS_DIR / "qa_prompt.txt").read_text(encoding="utf-8")
    prompt = prompt_tpl.replace("{script}", full_text).replace(
        "{ocr_sample}", "\n".join(ocr_lines[:30])
    )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=1024,
            response_mime_type="application/json",
        ),
    )
    raw = (response.text or "").strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        parsed = json.loads(cleaned)
        parsed["mode"] = "llm"
        return parsed
    except json.JSONDecodeError:
        return {
            "risk_score": 50,
            "warnings": ["LLM did not return parseable JSON."],
            "recommendations": [raw[:500]],
            "mode": "llm",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run QA on scenes.json.")
    parser.add_argument("--scenes", required=True, type=Path)
    parser.add_argument("--ocr", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-model", default="gemini-2.5-flash")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")
    load_dotenv()

    if not args.scenes.exists():
        logger.error("scenes file %s does not exist.", args.scenes)
        return 1

    try:
        scenes_payload = json.loads(args.scenes.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.error("scenes.json invalid: %s", exc)
        return 1

    ocr_lines: list[str] = []
    if args.ocr.exists():
        try:
            ocr_data = json.loads(args.ocr.read_text(encoding="utf-8"))
            ocr_lines = list(ocr_data.get("ocr_text_sample", [])) + list(
                ocr_data.get("emotional_cues", [])
            )
        except json.JSONDecodeError:
            logger.warning("OCR JSON unreadable; continuing without it.")

    if args.use_llm:
        try:
            report = llm_qa(scenes_payload, ocr_lines, args.llm_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM QA failed (%s); falling back to local heuristics.", exc)
            report = local_qa(scenes_payload, ocr_lines)
    else:
        report = local_qa(scenes_payload, ocr_lines)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "Wrote %s (risk=%s, warnings=%d, mode=%s)",
        args.output,
        report.get("risk_score"),
        len(report.get("warnings", [])),
        report.get("mode"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
