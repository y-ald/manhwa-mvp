#!/usr/bin/env bash
# Concatenate all segments and mux with the narration audio.
#
# Truncates to the shortest stream so we never expose silence at the end.
#
# Usage:
#   ./scripts/render_video.sh <segments_dir> <audio_path> <output_path>
set -euo pipefail

SEG_DIR="${1:-storage/temp/video_segments}"
AUDIO="${2:-storage/output/narration.wav}"
OUT="${3:-storage/output/final_video.mp4}"

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[render_video] ERROR: ffmpeg not found in PATH." >&2
    exit 1
fi

if [[ ! -d "$SEG_DIR" ]]; then
    echo "[render_video] ERROR: segments dir not found: $SEG_DIR" >&2
    exit 1
fi

if [[ ! -f "$AUDIO" ]]; then
    echo "[render_video] ERROR: audio file not found: $AUDIO" >&2
    exit 1
fi

mkdir -p "$(dirname "$OUT")"

CONCAT_LIST="$(mktemp)"
trap 'rm -f "$CONCAT_LIST"' EXIT

# Build concat list (FFmpeg concat demuxer wants absolute paths to be safe).
shopt -s nullglob
SEGMENTS=( "$SEG_DIR"/seg_*.mp4 )
shopt -u nullglob

if [[ ${#SEGMENTS[@]} -eq 0 ]]; then
    echo "[render_video] ERROR: no segments found in $SEG_DIR" >&2
    exit 1
fi

for seg in "${SEGMENTS[@]}"; do
    abs="$(cd "$(dirname "$seg")" && pwd)/$(basename "$seg")"
    echo "file '$abs'" >> "$CONCAT_LIST"
done

VIDEO_CONCAT="$(mktemp -u).mp4"

echo "[render_video] Concatenating ${#SEGMENTS[@]} segments..."
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$VIDEO_CONCAT" -loglevel error

echo "[render_video] Muxing with audio..."
# -shortest stops at min(audio_duration, video_duration).
ffmpeg -y -i "$VIDEO_CONCAT" -i "$AUDIO" \
    -c:v copy -c:a aac -b:a 192k \
    -shortest \
    -movflags +faststart \
    "$OUT" \
    -loglevel error

rm -f "$VIDEO_CONCAT"

echo "[render_video] Done: $OUT"
