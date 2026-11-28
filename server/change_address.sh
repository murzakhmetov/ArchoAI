#!/usr/bin/env bash
# The Pi got a new network address (other Wi-Fi, new DHCP lease): point everything at the new one.
#
#   bash server/change_address.sh            # uses the Pi's current address
#   bash server/change_address.sh 10.0.0.5   # or a given address
#
# Updates SUPABASE_URL in archoai_app/.env (app and scripts), the server address in monitoring.cpp
# (both copies), and rewrites the photo / model links stored in the database. Afterwards rebuild the
# app (copy the new archoai_app/.env to the build computer) and flash the ESP32 (server/esp32.sh upload).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NEW_IP="${1:-$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')}"
[ -n "$NEW_IP" ] || { echo "Cannot find the Pi's network address; pass it as an argument."; exit 1; }
NEW_URL="http://$NEW_IP:54321"

OLD_URL="$(grep -E '^SUPABASE_URL=' "$REPO/archoai_app/.env" | cut -d= -f2- | tr -d '[:space:]')"
echo "old address: ${OLD_URL:-none}"
echo "new address: $NEW_URL"

python3 -I - "$REPO" "$NEW_URL" <<'EOF'
import pathlib, re, sys
repo, url = pathlib.Path(sys.argv[1]), sys.argv[2]
env = repo / "archoai_app" / ".env"
text = env.read_text(encoding="utf-8")
text = re.sub(r"(?m)^SUPABASE_URL=.*$", lambda _: f"SUPABASE_URL={url}", text)
env.write_text(text, encoding="utf-8")
for firmware in (repo / "monitoring.cpp", repo / "scripts" / "monitoring.cpp"):
    source = firmware.read_text(encoding="utf-8")
    source = re.sub(r'(#define SUPABASE_URL\s+")http://[^/"]+(/rest/v1/sensor_data")',
                    lambda m: m.group(1) + url + m.group(2), source)
    firmware.write_text(source, encoding="utf-8")
print("updated archoai_app/.env and monitoring.cpp")
EOF

# Links of every artifact that point at another address of this server (".../storage/v1/object/public/...").
docker exec -i supabase_db_ArchoAI psql -U supabase_admin -d postgres -v ON_ERROR_STOP=1 -q -At <<SQL
begin;
set local session_replication_role = replica;  -- keeps updated_at
with changed as (
  update public.artifacts a set
    image_path = regexp_replace(image_path, '^http://[^/]+:54321/storage/', '$NEW_URL/storage/'),
    model_url = regexp_replace(model_url, '^http://[^/]+:54321/storage/', '$NEW_URL/storage/'),
    photo_urls = array(select regexp_replace(u, '^http://[^/]+:54321/storage/', '$NEW_URL/storage/')
                       from unnest(a.photo_urls) with ordinality t(u, n) order by n)
  where image_path ~ '^http://[^/]+:54321/storage/' or model_url ~ '^http://[^/]+:54321/storage/'
     or exists (select 1 from unnest(a.photo_urls) u where u ~ '^http://[^/]+:54321/storage/')
  returning 1)
select 'database links rewritten for ' || count(*) || ' artifacts' from changed;
commit;
SQL
echo "Next: copy archoai_app/.env to the app build computer and rebuild; flash the ESP32 (bash server/esp32.sh upload)."
