#!/usr/bin/env bash
# Set up (or repair) the ArchoAI server on this Raspberry Pi: the database and the file storage that
# replace the Supabase cloud project. Safe to run again: existing secrets and data are kept.
#
#   bash server/setup.sh          (from anywhere; asks for sudo where needed)
#
#   1. supabase/.env      random secrets of the stack, created once and never overwritten;
#   2. Docker             network "archoai_supabase": container ports are published on 127.0.0.1 only;
#   3. systemd            archoai-supabase.service starts the stack at boot,
#                         archoai-api-lan.service forwards port 54321 (all interfaces) to the API,
#                         archoai-replicate.timer runs the cloud replicator every 2 minutes (inert
#                         until server/replica.env exists, see server/README.md);
#   4. archoai_app/.env   SUPABASE_URL and SUPABASE_KEY of this Pi, for the app and the scripts.
#
# Then copy the printed address and key into monitoring.cpp (ESP32) if they changed.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
NETWORK=archoai_supabase
API_PORT=54321

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing: $1 -> $2" >&2; exit 1; }; }
need docker "sudo apt install docker.io"
need supabase "install the Supabase CLI .deb from https://github.com/supabase/cli/releases"
need socat "sudo apt install socat"
need python3 "sudo apt install python3"

echo "== 1/4 secrets (supabase/.env)"
python3 -I - "$REPO/supabase/.env" <<'EOF'
import os, secrets, sys
path = sys.argv[1]
if os.path.exists(path):
    print("   kept the existing secrets")
    sys.exit(0)
def token(n): return secrets.token_urlsafe(n).replace("-", "x").replace("_", "y")
values = {
    "ARCHOAI_JWT_SECRET": token(48),
    "ARCHOAI_PUBLISHABLE_KEY": f"sb_publishable_{token(16)[:22]}_{token(6)[:8]}",
    "ARCHOAI_SECRET_KEY": f"sb_secret_{token(16)[:22]}_{token(6)[:8]}",
}
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
os.write(fd, ("# Secrets of the self-hosted Supabase stack on this Raspberry Pi (git-ignored, never commit).\n"
              + "".join(f"{k}={v}\n" for k, v in values.items())).encode())
os.close(fd)
print("   created new secrets")
EOF

echo "== 2/4 docker"
if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx docker; then
  sudo usermod -aG docker "$RUN_USER"
  echo "   added $RUN_USER to the docker group (takes effect at the next login)"
fi
if ! sudo docker network inspect "$NETWORK" >/dev/null 2>&1; then
  sudo docker network create -o com.docker.network.bridge.host_binding_ipv4=127.0.0.1 "$NETWORK" >/dev/null
  echo "   created network $NETWORK (ports on 127.0.0.1 only)"
fi
# Containers on the default bridge also bind to 127.0.0.1 unless told otherwise.
if [ ! -f /etc/docker/daemon.json ]; then
  echo '{ "ip": "127.0.0.1" }' | sudo tee /etc/docker/daemon.json >/dev/null
  sudo systemctl restart docker
fi

echo "== 3/4 systemd services"
for unit in archoai-supabase.service archoai-api-lan.service archoai-replicate.service archoai-replicate.timer; do
  sed -e "s|@REPO@|$REPO|g" -e "s|@USER@|$RUN_USER|g" "$REPO/server/systemd/$unit" \
    | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now archoai-api-lan.service >/dev/null
sudo systemctl enable --now archoai-replicate.timer >/dev/null
echo "   starting the stack (the first start downloads about 2.5 GB of images)..."
sudo systemctl enable --now archoai-supabase.service >/dev/null
systemctl is-active archoai-supabase.service archoai-api-lan.service archoai-replicate.timer | paste -sd' ' | sed 's/^/   services: /'

# The scanner's GLB tool (smaller model copies for the 50 MB cloud) needs its Node packages.
if command -v node >/dev/null 2>&1 || [ -x "$HOME/.local/bin/node" ]; then
  (cd "$REPO/scripts/scripts/glb_tools" && PATH="$HOME/.local/bin:$PATH" npm ci --no-fund --no-audit --silent) \
    && echo "   glb_tools: Node packages installed"
fi

echo "== 4/4 archoai_app/.env"
LAN_IP="$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')"
LAN_IP="${LAN_IP:-$(hostname -I | awk '{print $1}')}"
python3 -I - "$REPO" "http://$LAN_IP:$API_PORT" <<'EOF'
import os, re, sys
repo, url = sys.argv[1], sys.argv[2]
secrets = dict(line.split("=", 1) for line in open(os.path.join(repo, "supabase", ".env"))
               if "=" in line and not line.startswith("#"))
key = secrets["ARCHOAI_PUBLISHABLE_KEY"].strip()
path = os.path.join(repo, "archoai_app", ".env")
if not os.path.exists(path):
    text = open(os.path.join(repo, "archoai_app", ".env.example"), encoding="utf-8").read()
else:
    text = open(path, encoding="utf-8").read()
def put(text, name, value):
    line = f"{name}={value}"
    if re.search(rf"(?m)^{name}=.*$", text):
        return re.sub(rf"(?m)^{name}=.*$", lambda _: line, text)
    return text.rstrip("\n") + "\n" + line + "\n"
text = put(text, "SUPABASE_URL", url)
text = put(text, "SUPABASE_KEY", key)
# The cloud project is not used any more; SUPABASE_URL wins anyway, this avoids confusion.
text = re.sub(r"(?m)^SUPABASE_PROJECT_REF=", "# SUPABASE_PROJECT_REF=", text)
fd = os.open(path + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
os.write(fd, text.encode()); os.close(fd)
os.replace(path + ".tmp", path)
print(f"   SUPABASE_URL={url}")
print(f"   SUPABASE_KEY=<publishable key of this Pi, {len(key)} chars>")
EOF

cat <<EOF

ArchoAI server is running on this Pi.
  API for phones, the ESP32 and the scripts: http://$LAN_IP:$API_PORT
  Database and storage stay on 127.0.0.1 (not reachable from the network).
  ESP32 (monitoring.cpp): SUPABASE_URL "http://$LAN_IP:$API_PORT/rest/v1/sensor_data",
                          SUPABASE_KEY = the SUPABASE_KEY value in archoai_app/.env
  Mobile app: build it with this archoai_app/.env (copy the file to the build computer).
  Give the Pi a fixed address in the router (DHCP reservation) so these links stay valid.
  Check everything: bash server/check_all.sh
EOF
