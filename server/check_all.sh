#!/usr/bin/env bash
# Checks the whole ArchoAI system on this Raspberry Pi and prints PASS / FAIL / SKIP per part.
#
#   bash server/check_all.sh          # everything (about 5-10 minutes)
#   QUICK=1 bash server/check_all.sh  # without the slow parts (app end-to-end run, web build, ESP32)
#
# Test data is removed again; the live rows and files are never changed (see each test's notes).
# Logs of failed parts are printed at the end; all logs stay in the folder shown at the start.
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO/scripts/scripts/venv/bin/python"
FLUTTER="$HOME/development/flutter/bin/flutter"
export PATH="$HOME/.local/bin:$HOME/development/flutter/bin:$PATH" FLUTTER_SUPPRESS_ANALYTICS=true
LOGS="$(mktemp -d /tmp/archoai-check.XXXXXX)"
results=()
failed=()

step() {   # name, command...
  local name="$1"; shift
  local log="$LOGS/$(echo "$name" | tr -c 'A-Za-z0-9' '_').log"
  printf '%-48s' "$name"
  if ( "$@" ) >"$log" 2>&1; then   # a subshell: "cd" and "exit" stay inside the step
    echo "PASS"; results+=("PASS  $name")
  elif [ $? -eq 77 ]; then
    echo "SKIP ($(tail -1 "$log"))"; results+=("SKIP  $name")
  else
    echo "FAIL"; results+=("FAIL  $name"); failed+=("$name|$log")
  fi
}
skip() { echo "$1"; exit 77; }   # only used inside steps, which run in a subshell

services() {
  systemctl is-active --quiet archoai-supabase.service || { echo "archoai-supabase.service is not active"; return 1; }
  systemctl is-active --quiet archoai-api-lan.service || { echo "archoai-api-lan.service is not active"; return 1; }
  local unhealthy
  unhealthy="$(docker ps --filter label=com.supabase.cli.project=ArchoAI --format '{{.Names}} {{.Status}}' | grep -v healthy | grep -v supabase_rest || true)"
  [ -z "$unhealthy" ] || { echo "unhealthy: $unhealthy"; return 1; }
  docker ps --filter label=com.supabase.cli.project=ArchoAI --format '{{.Ports}}' | grep -q '0\.0\.0\.0\|:::' \
    && { echo "a container port is published on all interfaces"; return 1; }
  return 0
}

camera() {
  "$PY" - "$REPO" <<'EOF'
import os, sys, cv2
from dotenv import dotenv_values
index = int(dotenv_values(os.path.join(sys.argv[1], "archoai_app", ".env")).get("CAMERA_INDEX") or 0)
cap = cv2.VideoCapture(index, cv2.CAP_V4L2)
if not cap.isOpened():
    print(f"no camera at index {index}"); sys.exit(77)
for _ in range(10):
    ok, frame = cap.read()
cap.release()
print(f"camera {index}: {frame.shape[1]}x{frame.shape[0]}" if ok else "camera opened but gave no frame")
sys.exit(0 if ok else 1)
EOF
}

web_ui() {
  cd "$REPO/scripts/scripts/web-deepcrack" || return 1
  [ -d node_modules ] || npm ci --no-fund --no-audit || return 1
  CI=true npm test -- --watchAll=false || return 1
  [ -n "${QUICK:-}" ] || CI=true npm run build
}

flutter_checks() {
  [ -x "$FLUTTER" ] || skip "Flutter is not installed in ~/development/flutter"
  cd "$REPO/archoai_app" || return 1
  "$FLUTTER" --no-version-check pub get && "$FLUTTER" --no-version-check analyze && "$FLUTTER" --no-version-check test
}

app_e2e() {
  [ -n "${QUICK:-}" ] && skip "QUICK=1"
  [ -x "$FLUTTER" ] || skip "Flutter is not installed"
  command -v xvfb-run >/dev/null || skip "xvfb is not installed"
  bash "$REPO/server/tests/run_app_e2e.sh"
}

esp32() {
  [ -n "${QUICK:-}" ] && skip "QUICK=1"
  cmp -s "$REPO/monitoring.cpp" "$REPO/scripts/monitoring.cpp" || { echo "the two copies of monitoring.cpp differ"; return 1; }
  if [ -x "$HOME/.local/opt/platformio-venv/bin/pio" ]; then
    bash "$REPO/server/esp32.sh" build
  elif command -v arduino-cli >/dev/null && arduino-cli core list 2>/dev/null | grep -q '^esp32:esp32'; then
    local sketch="$LOGS/monitoring"
    mkdir -p "$sketch" && cp "$REPO/monitoring.cpp" "$sketch/monitoring.ino"
    arduino-cli compile --fqbn esp32:esp32:esp32 "$sketch"
  else
    skip "neither PlatformIO nor the arduino-cli ESP32 core is installed"
  fi
}

echo "ArchoAI system check ($(date '+%Y-%m-%d %H:%M')), logs in $LOGS"
step "server services and network exposure"       services
step "camera"                                      camera
step "python unit tests (scripts)"                 "$PY" -B -m unittest discover -s "$REPO/scripts/scripts/tests"
step "server API: ESP32, scanner and app contracts" "$PY" -B -m unittest discover -s "$REPO/server/tests" -p test_server_api.py
step "scanner end to end (withbutton*.py)"         "$PY" -B -m unittest discover -s "$REPO/server/tests" -p test_scan_pipeline.py
step "crack detection API (main.py)"               "$PY" -B -m unittest discover -s "$REPO/server/tests" -p test_crack_api.py
step "backup and restore"                          "$PY" -B -m unittest discover -s "$REPO/server/tests" -p test_backup.py
step "database policies (supabase/tests/smoke.sh)" bash "$REPO/supabase/tests/smoke.sh"
step "migrations describe the database (drift)"   python3 "$REPO/supabase/tests/migration_drift.py"
step "web UI (web-deepcrack)"                      web_ui
step "mobile app: analyze + unit tests"            flutter_checks
step "mobile app end to end on the Pi server"      app_e2e
step "ESP32 firmware compiles (monitoring.cpp)"    esp32

echo
printf '%s\n' "${results[@]}" | sort | uniq -c -w4 | awk '{print $2": "$1}' | paste -sd' '
for entry in "${failed[@]}"; do
  echo; echo "---- ${entry%%|*} (last lines of ${entry#*|})"; tail -25 "${entry#*|}"
done
[ ${#failed[@]} -eq 0 ]
