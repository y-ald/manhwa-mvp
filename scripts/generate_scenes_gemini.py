"""Generate the French narration as a STRUCTURED SCENES JSON via Gemini.

Replaces the v0.1 plain-text `generate_script_gemini.py`. Output schema:

    {
      "title": "...",
      "language": "fr",
      "scenes": [
        {
          "id": 1,
          "type": "hook|context|rising|twist|climax|ending",
          "text": "...",
          "tone": "mystérieux|sombre|action|émotion|neutre",
          "image_keywords": ["..."],
          "duration_hint_sec": 8
        }
      ]
    }

If Gemini returns malformed JSON, we retry once with a stricter prompt suffix,
then fall back to a single-scene structure that wraps whatever text we got.

Usage:
    python -m scripts.generate_scenes_gemini \
        --context storage/temp/context.json \
        --output storage/temp/scenes.json \
        --model gemini-2.5-pro \
        --min-scenes 5 --max-scenes 10
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logger = logging.getLogger("scenes")

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

VALID_TONES = {"mystérieux", "sombre", "action", "émotion", "neutre"}
VALID_TYPES = {"hook", "context", "rising", "twist", "climax", "ending"}


def load_prompts() -> tuple[str, str]:
    system = (PROMPTS_DIR / "narrative_scenes_system.txt").read_text(encoding="utf-8")
    user = (PROMPTS_DIR / "narrative_scenes_user.txt").read_text(encoding="utf-8")
    return system, user


def call_gemini(system_prompt: str, user_prompt: str, model: str) -> str:
    from google import genai
    from google.genai import types

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in environment.")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        system_instruction=system_prompt,
        temperature=0.85,
        max_output_tokens=6144,
        response_mime_type="application/json",
    )
    response = client.models.generate_content(
        model=model, contents=user_prompt, config=config
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    return text


def strip_fences(raw: str) -> str:
    """Some models still wrap JSON in ```json fences despite mime type."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    return cleaned.strip()


def normalize_scene(scene: dict[str, Any], idx: int, default_dur: int, max_dur: int) -> dict[str, Any]:
    """Coerce a single scene into a safe shape."""
    scene_id = scene.get("id", idx)
    scene_type = scene.get("type", "context")
    if scene_type not in VALID_TYPES:
        scene_type = "context"
    tone = scene.get("tone", "neutre")
    if tone not in VALID_TONES:
        tone = "neutre"
    text = (scene.get("text") or "").strip()
    keywords = scene.get("image_keywords") or []
    if not isinstance(keywords, list):
        keywords = [str(keywords)]
    keywords = [str(k).strip() for k in keywords if str(k).strip()][:5]
    try:
        duration = int(scene.get("duration_hint_sec", default_dur))
    except (TypeError, ValueError):
        duration = default_dur
    duration = max(2, min(duration, max_dur))
    return {
        "id": scene_id,
        "type": scene_type,
        "text": text,
        "tone": tone,
        "image_keywords": keywords,
        "duration_hint_sec": duration,
    }


def normalize_payload(
    payload: dict[str, Any], default_dur: int, max_dur: int
) -> dict[str, Any]:
    scenes_in = payload.get("scenes") or []
    if not isinstance(scenes_in, list) or not scenes_in:
        raise ValueError("payload has no scenes[] array.")
    scenes = [
        normalize_scene(s, i + 1, default_dur, max_dur)
        for i, s in enumerate(scenes_in)
        if isinstance(s, dict) and (s.get("text") or "").strip()
    ]
    if not scenes:
        raise ValueError("no scene with non-empty text.")
    return {
        "title": payload.get("title") or "",
        "language": payload.get("language") or "fr",
        "scenes": scenes,
    }


def fallback_single_scene(raw_text: str, default_dur: int) -> dict[str, Any]:
    """Last-resort: wrap the raw text into a single scene."""
    text = re.sub(r"\s+", " ", raw_text).strip()
    return {
        "title": "",
        "language": "fr",
        "scenes": [
            {
                "id": 1,
                "type": "context",
                "text": text[:1500],
                "tone": "neutre",
                "image_keywords": [],
                "duration_hint_sec": default_dur,
            }
        ],
    }


def generate(
    context_path: Path,
    model: str,
    min_scenes: int,
    max_scenes: int,
    default_dur: int,
    max_dur: int,
) -> dict[str, Any]:
    context = json.loads(context_path.read_text(encoding="utf-8"))
    system, user_tpl = load_prompts()
    user = (
        user_tpl.replace("{min_scenes}", str(min_scenes))
        .replace("{max_scenes}", str(max_scenes))
        .replace(
            "{context_json}", json.dumps(context, ensure_ascii=False, indent=2)
        )
    )

    logger.info("Calling Gemini model=%s", model)
    raw = call_gemini(system, user, model)
    cleaned = strip_fences(raw)

    try:
        payload = json.loads(cleaned)
        return normalize_payload(payload, default_dur, max_dur)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("first parse failed (%s); retrying with stricter prompt.", exc)

    retry_user = (
        user
        + "\n\nIMPORTANT: ta dernière réponse n'était pas du JSON valide. "
        "Retourne UNIQUEMENT du JSON conforme au schéma, rien d'autre."
    )
    try:
        raw2 = call_gemini(system, retry_user, model)
        payload2 = json.loads(strip_fences(raw2))
        return normalize_payload(payload2, default_dur, max_dur)
    except Exception as exc:  # noqa: BLE001
        logger.error("retry also failed (%s); falling back to single scene.", exc)
        return fallback_single_scene(raw, default_dur)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate narration scenes via Gemini.")
    parser.add_argument("--context", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default="gemini-2.5-pro")
    parser.add_argument("--min-scenes", type=int, default=5)
    parser.add_argument("--max-scenes", type=int, default=10)
    parser.add_argument("--default-duration-hint", type=int, default=8)
    parser.add_argument("--max-duration-hint", type=int, default=30)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="[%(name)s] %(levelname)s %(message)s")
    load_dotenv()

    try:
        scenes = generate(
            args.context,
            args.model,
            args.min_scenes,
            args.max_scenes,
            args.default_duration_hint,
            args.max_duration_hint,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("scene generation failed: %s", exc)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(scenes, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "Wrote %s (%d scene(s))",
        args.output,
        len(scenes.get("scenes", [])),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
