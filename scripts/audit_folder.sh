#!/usr/bin/env bash
# Batch-audit a folder of recordings for aircraft-Doppler leaks, one JSON per file.
#
# Runs scripts/audit_video.py on every video in SRC_DIR, writing OUT_DIR/<name>.json.
# Files that already have a non-empty JSON are skipped, so the batch resumes after a stop.
# Audio only: no flight data, dates, or location — the reports are built from these JSONs.
#
# Usage: audit_folder.sh SRC_DIR [OUT_DIR] [-- extra args for audit_video.py]
#   OUT_DIR defaults to ./audits. Set PYTHON to override .venv/bin/python.
# Example: audit_folder.sh ~/Code/kairuichan_vods/videos audits -- --windows 16
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PYTHON:-$ROOT/.venv/bin/python}
SRC=${1:-}
OUT=${2:-$ROOT/audits}
[ -n "$SRC" ] || { echo "usage: audit_folder.sh SRC_DIR [OUT_DIR] [-- audit_video.py args]" >&2; exit 2; }
[ -d "$SRC" ] || { echo "no such folder: $SRC" >&2; exit 1; }
[ -x "$PY" ] || { echo "no python at $PY: create the venv or set PYTHON" >&2; exit 1; }
shift || true; [ "${1:-}" = "$OUT" ] && shift || true
[ "${1:-}" = "--" ] && shift || true   # anything left is passed through to audit_video.py

mkdir -p "$OUT"
shopt -s nullglob nocaseglob
files=("$SRC"/*.{mp4,mkv,mov,webm,m4v,flv,ts})
shopt -u nocaseglob
[ ${#files[@]} -gt 0 ] || { echo "no video files in $SRC" >&2; exit 1; }

done_n=0 skip_n=0 fail_n=0 failed=""
for f in "${files[@]}"; do
  name=$(basename "$f"); name=${name%.*}
  out="$OUT/$name.json"
  if [ -s "$out" ]; then echo "skip  $name (already audited)"; skip_n=$((skip_n+1)); continue; fi
  echo "audit $name"
  # Write to a temp file so an interrupted run never leaves a half-written JSON that looks done.
  if "$PY" "$ROOT/scripts/audit_video.py" "$f" --report "$out.part" "$@"; then
    mv "$out.part" "$out"; done_n=$((done_n+1))
  else
    rm -f "$out.part"; echo "  FAILED: $name" >&2; fail_n=$((fail_n+1)); failed="$failed $name"
  fi
done

echo "---"
echo "audited $done_n, skipped $skip_n, failed $fail_n -> $OUT"
[ -n "$failed" ] && { echo "failed:$failed" >&2; exit 1; }
exit 0
