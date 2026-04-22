#!/usr/bin/env bash
# Generate narration.wav from script.txt using Piper TTS.
#
# Usage:
#   ./scripts/tts_piper.sh <script_path> <model_path> <output_wav>
#
# Example:
#   ./scripts/tts_piper.sh storage/temp/script.txt \
#       storage/models/fr_FR-siwis-medium.onnx \
#       storage/output/narration.wav
set -euo pipefail

SCRIPT_PATH="${1:-storage/temp/script.txt}"
MODEL_PATH="${2:-storage/models/fr_FR-siwis-medium.onnx}"
OUTPUT_PATH="${3:-storage/output/narration.wav}"

if ! command -v piper >/dev/null 2>&1; then
    echo "[tts_piper] ERROR: 'piper' binary not found in PATH." >&2
    echo "Install from https://github.com/rhasspy/piper/releases" >&2
    exit 1
fi

if [[ ! -f "$SCRIPT_PATH" ]]; then
    echo "[tts_piper] ERROR: script not found: $SCRIPT_PATH" >&2
    exit 1
fi

if [[ ! -f "$MODEL_PATH" ]]; then
    echo "[tts_piper] ERROR: model not found: $MODEL_PATH" >&2
    echo "Download with: wget https://huggingface.co/rhasspy/piper-voices/resolve/main/fr/fr_FR/siwis/medium/fr_FR-siwis-medium.onnx" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"

echo "[tts_piper] Generating $OUTPUT_PATH..."
cat "$SCRIPT_PATH" | piper --model "$MODEL_PATH" --output_file "$OUTPUT_PATH"

echo "[tts_piper] Done: $OUTPUT_PATH"
