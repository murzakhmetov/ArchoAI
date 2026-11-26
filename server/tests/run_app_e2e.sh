#!/usr/bin/env bash
# Runs the mobile app end to end against the ArchoAI server on this Pi, then removes what the
# test created (the selftest-app-* account and the test sensor reading).
#
# The app runs as a Linux desktop build (same Dart code as the phone app) on a virtual display,
# so no window opens. Needs: Flutter (~/development/flutter), xvfb, clang, cmake, ninja,
# libgtk-3-dev and lld.
#
#   bash server/tests/run_app_e2e.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export PATH="$HOME/development/flutter/bin:$PATH" FLUTTER_SUPPRESS_ANALYTICS=true

cd "$REPO/archoai_app"
xvfb-run -a -s "-screen 0 1280x1024x24" \
  flutter --no-version-check test integration_test/pi_server_test.dart -d linux
status=$?

python3 -I - "$REPO" <<'EOF'
import json, pathlib, sys, urllib.request
repo = pathlib.Path(sys.argv[1])
env = lambda p: dict(l.split("=", 1) for l in p.read_text().splitlines() if "=" in l and not l.lstrip().startswith("#"))
secret = env(repo / "supabase" / ".env")["ARCHOAI_SECRET_KEY"].strip()
base = "http://127.0.0.1:54331"
headers = {"apikey": secret, "Authorization": f"Bearer {secret}"}

def call(method, path):
    request = urllib.request.Request(base + path, method=method, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        body = response.read()
        return json.loads(body) if body else None

users = call("GET", "/auth/v1/admin/users?per_page=1000")["users"]
test_users = [u for u in users if (u.get("email") or "").startswith("selftest-app-")]
for user in test_users:
    call("DELETE", f"/auth/v1/admin/users/{user['id']}")
call("DELETE", "/rest/v1/sensor_data?humidity=eq.33.25&air_quality=eq.3999")
call("DELETE", "/rest/v1/sensor_data?temperature=eq.21.5&humidity=eq.33.25")  # older test runs
print(f"cleanup: removed {len(test_users)} test account(s) and the test reading")
EOF
exit $status
