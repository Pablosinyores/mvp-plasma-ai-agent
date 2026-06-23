#!/usr/bin/env bash
# record_demo_fullscreen.sh — record the WHOLE screen (Chrome dashboard left + this Terminal right)
# while the full Phase 1→6 demo runs. Native macOS screencapture, no extra installs.
#
# RUN THIS IN THE RIGHT-HAND macOS Terminal, with Chrome on the left at http://localhost:8080.
# Assumes the stack is already UP + clean:
#   docker compose down && rm -rf backend/.agent contracts/deployments/local.json && make up && make model
#
# Output: recordings/demo-fullscreen.mov
set -uo pipefail
cd "$(dirname "$0")/.."
. .venv/bin/activate 2>/dev/null || true

OUT="recordings/demo-fullscreen.mov"
mkdir -p recordings
export MODEL_BACKEND="${MODEL_BACKEND:-llamacpp}"
export SKIP_BG=1   # this wrapper owns the worker + dashboard

REC_PID=""; WORKER_PID=""; DASH_PID=""
cleanup() {
  echo "» stopping demo + recording…"
  [ -n "$REC_PID" ] && kill -INT "$REC_PID" 2>/dev/null   # finalize the .mov
  [ -n "$REC_PID" ] && wait "$REC_PID" 2>/dev/null
  [ -n "$WORKER_PID" ] && kill "$WORKER_PID" 2>/dev/null
  [ -n "$DASH_PID" ] && kill "$DASH_PID" 2>/dev/null
  pkill -f studio_worker.py 2>/dev/null; pkill -f "studio.py dashboard" 2>/dev/null
}
trap cleanup EXIT INT TERM

echo "== preflight =="
curl -s --max-time 3 localhost:8081/v1/models 2>/dev/null | grep -q Qwen \
  && echo "model: READY" || echo "model: NOT READY (run 'make model' first; demo will still run on stub if you set MODEL_BACKEND=stub)"

echo "== start worker + dashboard =="
python3 backend/studio_worker.py > /tmp/studio_worker.log 2>&1 & WORKER_PID=$!
python3 backend/cli/studio.py dashboard  > /tmp/studio_dash.log   2>&1 & DASH_PID=$!
sleep 4

echo "== open Chrome on the dashboard =="
open -a "Google Chrome" "http://localhost:8080" 2>/dev/null || open "http://localhost:8080"
echo "Arrange now: Chrome (left)  |  this Terminal (right). Recording starts in 5s…"
sleep 5

echo "== START full-screen recording -> $OUT =="
# -v video, -V<sec> hard cap (safety), recording the full main display; killed early on cleanup
screencapture -v -V 600 "$OUT" >/tmp/screencap.log 2>&1 & REC_PID=$!
sleep 2

# === run the demo (phases stream here; dashboard updates in Chrome) ===
PACE="${PACE:-2}" ./scripts/demo_record.sh

echo "== demo finished — stopping recording =="
# cleanup() finalizes the .mov and stops bg procs
trap - EXIT INT TERM
cleanup
sleep 1
echo "✅ saved: $(pwd)/$OUT"
ls -lh "$OUT" 2>/dev/null
