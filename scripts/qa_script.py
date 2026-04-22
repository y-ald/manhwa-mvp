"""Minimal QA over the generated script.

Two modes:
- Local heuristics (default): cheap checks (OCR overlap, very-short-line ratio,
  obvious dialogue markers).
- LLM second pass (opt-in via --use-llm + GEMINI_API_KEY): defers to Gemini
  using prompts/qa_prompt.txt.

Output (qa.json):
    {
      "risk_score": int,
      "warnings": [str, ...],
      "recommendations": [str, ...],
      "mode": "local" | "llm"
    }

Usage:
    python -m scripts.qa_script --script storage/temp/script.txt \
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

# Patterns that hint at quoted dialogue.
DIALOGUE_RE = re.compile(r'(["«»“”])([^"«»“”]{5,})\1|^[\-—]\s+\w', re.MULTILINE)


def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lower())


def local_qa(script: str, ocr_lines: list[str]) -> dict[str, object]:
    warnings: list[str] = []
    recommendations: list[str] = []
    score = 0

    norm_script = normalize(script)

    # 1) OCR overlap detection.
    overlap_hits = 0
    for line in ocr_lines:
        n = normalize(line)
        if len(n) >= 12 and n in norm_script:
            overlap_hits += 1
            warnings.append(f"Phrase OCR potentiellement reprise telle quelle: {line!r}")
    if overlap_hits:
        score += min(40, 8 * overlap_hits)
        recommendations.append("Reformuler les passages issus de l'OCR.")

    # 2) Short-line ratio (descriptive bullet-point feel).
    lines = [ln for ln in script.splitlines() if ln.strip()]
    if lines:
        short_ratio = sum(1 for ln in lines if len(ln) < 40) / len(lines)
        if short_ratio > 0.4:
            score += 15
            warnings.append(
                f"{int(short_ratio * 100)}% des lignes sont très courtes (style descriptif)."
            )
            recommendations.append(
                "Privilégier des paragraphes longs, fluides, type narration orale."
            )

    # 3) Dialogue markers.
    dialogue_matches = DIALOGUE_RE.findall(script)
    if dialogue_matches:
        score += min(25, 5 * len(dialogue_matches))
        warnings.append(
            f"{len(dialogue_matches)} marque(s) de dialogue direct détectée(s)."
        )
        recommendations.append("Remplacer les dialogues par du discours indirect.")

    # 4) Word count guard rails.
    word_count = len(script.split())
    if word_count < 500:
        score += 10
        warnings.append(f"Script trop court ({word_count} mots, cible 600-900).")
    elif word_count > 1100:
        score += 5
        warnings.append(f"Script trop long ({word_count} mots, cible 600-900).")

    return {
        "risk_score": min(score, 100),
        "warnings": warnings,
        "recommendations": recommendations,
        "mode": "local",
    }


def llm_qa(script: str, ocr_lines: list[str], model: str) -> dict[str, object]:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set; cannot use --use-llm.")

    prompt_tpl = (PROMPTS_DIR / "qa_prompt.txt").read_text(encoding="utf-8")
    prompt = prompt_tpl.replace("{script}", script).replace(
        "{ocr_sample}", "\n".join(ocr_lines[:30])
    )

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(temperature=0.2, max_output_tokens=1024),
    )
    raw = (response.text or "").strip()

    # The prompt asks for strict JSON; try to parse, fall back to a wrapped error.
    try:
        # Strip a possible ```json fence.
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw, flags=re.MULTILINE).strip()
        parsed = json.loads(cleaned)
        parsed["mode"] = "llm"
        return parsed
    except json.JSONDecodeError:
        logger.warning("LLM did not return strict JSON, wrapping raw output.")
        return {
            "risk_score": 50,
            "warnings": ["LLM did not return parseable JSON."],
            "recommendations": [raw[:500]],
            "mode": "llm",
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run QA on the generated script.")
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--ocr", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--use-llm", action="store_true")
    parser.add_argument("--llm-model", default="gemini-2.5-flash")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")
    load_dotenv()

    if not args.script.exists():
        logger.error("Script file %s does not exist.", args.script)
        return 1

    script = args.script.read_text(encoding="utf-8")
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
            report = llm_qa(script, ocr_lines, args.llm_model)
        except Exception as exc:  # noqa: BLE001
            logger.warning("LLM QA failed (%s); falling back to local heuristics.", exc)
            report = local_qa(script, ocr_lines)
    else:
        report = local_qa(script, ocr_lines)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(
        "Wrote %s (risk=%s, warnings=%s, mode=%s)",
        args.output,
        report.get("risk_score"),
        len(report.get("warnings", [])),
        report.get("mode"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
