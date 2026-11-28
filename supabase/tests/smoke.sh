#!/usr/bin/env bash
# Smoke tests for the Supabase setup. Every check runs inside a transaction that is rolled back, so
# nothing is left behind (storage rows are inserted straight into storage.objects inside that
# transaction: this tests the POLICIES, not the Storage API). Each check creates the rows it needs, so
# it also works on an empty project.
#
#   bash supabase/tests/smoke.sh                 # the server on this Raspberry Pi (local stack)
#   REMOTE=<project ref> bash supabase/tests/smoke.sh   # a Supabase cloud project instead (logged-in CLI;
#                                                       # the CLI asks before it runs statements there)
#
# One CLI call per check: a full run takes several minutes. Do not run other `supabase db query`
# commands at the same time (the CLI logs in with a temporary role per call and calls can collide;
# the script retries such a collision a few times).
# Prints PASS or FAIL per check and exits non-zero if anything failed or could not run.

REMOTE="${REMOTE:-${REF:-}}"   # REF=<ref> is the older name of REMOTE
TARGET=()
[ -n "$REMOTE" ] && TARGET=("--remote=$REMOTE")
fail=0

if [ -n "$REMOTE" ] && ! command -v supabase >/dev/null 2>&1; then
  echo "FAIL  the supabase CLI is not on PATH"
  exit 2
fi
if [ -z "$REMOTE" ] && ! python3 -c 'import psycopg' 2>/dev/null; then
  echo "FAIL  python3-psycopg is missing (sudo apt install python3-psycopg)"
  exit 2
fi
echo "target: ${REMOTE:-local stack on this Pi}"

# Runs SQL through the Management API. Success output contains a "rows" key, an error contains "_tag".
run() {
  local out attempt=0
  while true; do
    if [ -n "$REMOTE" ]; then
      out="$(supabase db query "${TARGET[@]}" "$1" 2>&1)"
    else
      out="$(python3 "$(dirname "${BASH_SOURCE[0]}")/local_sql.py" "$1" 2>&1)"
    fi
    if echo "$out" | grep -qE 'DbConfigConnectTempRoleError|cli_login_postgres' && [ "$attempt" -lt 3 ]; then
      attempt=$((attempt + 1)); sleep 4; continue
    fi
    echo "$out"; return 0
  done
}

ok_rows() {         # name, sql, [extended regex that must match the output]
  out="$(run "$2")"
  if echo "$out" | grep -q '"_tag"' || ! echo "$out" | grep -q '"rows"'; then
    echo "FAIL  $1"; echo "$out" | head -4 | sed 's/^/      /'; fail=1; return
  fi
  if [ -n "$3" ] && ! echo "$out" | tr -d '\n' | grep -Eq "$3"; then
    echo "FAIL  $1 (output did not match $3)"; echo "$out" | head -6 | sed 's/^/      /'; fail=1; return
  fi
  echo "PASS  $1"
}

must_fail() {       # name, sql, extended regex that must appear in the error text
  out="$(run "$2")"
  if echo "$out" | grep -q '"_tag"' && echo "$out" | grep -Eq "$3"; then
    echo "PASS  $1"
  else
    echo "FAIL  $1 (expected an error matching $3)"; echo "$out" | head -4 | sed 's/^/      /'; fail=1
  fi
}

INS="insert into public.sensor_data(temperature,humidity,air_quality) values"
OBJ="insert into storage.objects (bucket_id, name, metadata) values"
# What the Storage API does for an upload with upsert: INSERT ... ON CONFLICT DO UPDATE on the current version.
OC='on conflict (bucket_id, name collate "C") where (archived_at is null) do update set metadata = excluded.metadata'

echo "--- sensor_data (anon = the ESP32 with the public key)"
ok_rows   "anon inserts a valid reading" \
  "begin; set local role anon; $INS (24.5,45,300) returning id; rollback;" '"id"'
must_fail "anon humidity 145 is refused" \
  "begin; set local role anon; $INS (24.5,145,300); rollback;" "row-level security"
ok_rows   "anon raw ADC maximum 4095 is accepted" \
  "begin; set local role anon; $INS (24.5,45,4095) returning id; rollback;" '"id"'
must_fail "anon air_quality 4096 is refused" \
  "begin; set local role anon; $INS (24.5,45,4096); rollback;" "row-level security"
must_fail "anon cannot set created_at" \
  "begin; set local role anon; insert into public.sensor_data(temperature,humidity,air_quality,created_at) values (24.5,45,300,'2100-01-01'); rollback;" "permission denied"
must_fail "anon cannot set id" \
  "begin; set local role anon; insert into public.sensor_data(id,temperature,humidity,air_quality) values (999999999,24.5,45,300); rollback;" "permission denied"
must_fail "anon rate limit: a second insert within 2 s is refused" \
  "begin; set local role anon; $INS (24.5,45,300); $INS (24.5,45,301); rollback;" "at most one anonymous insert"
must_fail "signed-in users cannot insert sensor_data (the app only reads it, the device is anon)" \
  "begin; set local role authenticated; $INS (24.5,45,300); rollback;" "permission denied"
INS6="insert into public.sensor_data(temperature,humidity,air_quality,soil_moisture,soil_temperature,air_ppm) values"
ok_rows   "anon inserts a reading with soil moisture, soil temperature and air ppm" \
  "begin; set local role anon; $INS6 (24.5,45,300,37.5,18.25,612) returning id; rollback;" '"id"'
ok_rows   "anon may leave the soil and ppm values empty (null)" \
  "begin; set local role anon; $INS6 (24.5,45,300,null,null,null) returning id; rollback;" '"id"'
must_fail "anon soil moisture 101 is refused" \
  "begin; set local role anon; $INS6 (24.5,45,300,101,18,612); rollback;" "row-level security"
must_fail "anon soil temperature 126 is refused" \
  "begin; set local role anon; $INS6 (24.5,45,300,40,126,612); rollback;" "row-level security"
must_fail "anon air ppm 10001 is refused" \
  "begin; set local role anon; $INS6 (24.5,45,300,40,18,10001); rollback;" "row-level security"
ok_rows   "a future-dated row written by another role cannot lock the device out" \
  "begin; insert into public.sensor_data(temperature,humidity,air_quality,created_at) values (24.5,45,300,'2100-01-01'); set local role anon; $INS (24.5,45,300) returning id; rollback;" '"id"'

echo "--- artifacts"
# A constant UPDATE without WHERE reads no column, so no SELECT policy is involved: only an UPDATE policy could let it through.
ok_rows   "anon cannot update artifacts" \
  "begin; insert into public.artifacts(name) values ('smoke-keep'); set local role anon; update public.artifacts set name = 'smoke-changed'; reset role; select count(*) as changed from public.artifacts where name = 'smoke-changed'; rollback;" '"changed": *0'
ok_rows   "authenticated updates artifacts (more than 0 rows)" \
  "begin; insert into public.artifacts(name) values ('smoke'); set local role authenticated; with u as (update public.artifacts set name = name returning 1) select count(*) as updated_rows from u; rollback;" '"updated_rows": *[1-9]'

echo "--- storage policies"
# No RETURNING here: anon has no SELECT policy on storage.objects, and RETURNING needs one.
ok_rows   "anon may upload a .glb into a model batch" \
  "begin; set local role anon; $OBJ ('models-01','smoke-test.glb','{\"size\":24}'); rollback;" '"rows": *\[\]'
must_fail "anon cannot upload a non-.glb into a model batch" \
  "begin; set local role anon; $OBJ ('models-01','smoke-test.txt','{\"size\":24}'); rollback;" "row-level security"
must_fail "anon cannot use a '..' path segment" \
  "begin; set local role anon; $OBJ ('models-01','a/../b.glb','{\"size\":24}'); rollback;" "row-level security"
ok_rows   "signed-in users can upsert a .glb (insert, then replace it): what the app's upload does" \
  "begin; set local role authenticated; $OBJ ('models-01','smoke-upsert.glb','{\"size\":24}') $OC; $OBJ ('models-01','smoke-upsert.glb','{\"size\":25}') $OC; select metadata->>'size' as size from storage.objects where bucket_id='models-01' and name='smoke-upsert.glb'; rollback;" '"size": *"25"'
must_fail "anon cannot upsert into a model batch (an upsert needs SELECT and UPDATE policies)" \
  "begin; set local role anon; $OBJ ('models-01','smoke-upsert-anon.glb','{\"size\":24}') $OC; rollback;" "row-level security"
ok_rows   "signed-in users can replace a model that lives in a FULL batch (no second copy needed)" \
  "begin; $OBJ ('models-01','smoke-full.glb','{\"size\":24}'); update public.model_buckets set capacity_bytes = 0 where bucket_id='models-01'; set local role authenticated; $OBJ ('models-01','smoke-full.glb','{\"size\":25}') $OC; reset role; select metadata->>'size' as size from storage.objects where bucket_id='models-01' and name='smoke-full.glb'; rollback;" '"size": *"25"'
must_fail "a NEW file is still refused in a full batch" \
  "begin; update public.model_buckets set capacity_bytes = 0 where bucket_id='models-01'; set local role authenticated; $OBJ ('models-01','smoke-new.glb','{\"size\":24}'); rollback;" "row-level security"
must_fail "the total cap also stops the OLD image bucket" \
  "begin; update public.app_limits set value_bigint = 1 where key='storage_total_cap_bytes'; set local role anon; $OBJ ('artifact-images','smoke.jpg','{\"size\":24}'); rollback;" "row-level security"
must_fail "the total cap also stops the legacy 3d1 bucket" \
  "begin; update public.app_limits set value_bigint = 1 where key='storage_total_cap_bytes'; set local role anon; $OBJ ('3d1','smoke.glb','{\"size\":24}'); rollback;" "row-level security"
# Same idea: a constant UPDATE of every object. The model-batch row must change, the legacy 3d1 row must not.
ok_rows   "signed-in users can update model-batch objects but not objects of the legacy bucket 3d1" \
  "begin; $OBJ ('3d1','smoke-legacy.glb','{\"size\":1}'), ('models-01','smoke-batch.glb','{\"size\":1}'); set local role authenticated; update storage.objects set metadata = '{\"size\":2}'::jsonb; reset role; select (select metadata->>'size' from storage.objects where bucket_id='3d1' and name='smoke-legacy.glb') as legacy_size, (select metadata->>'size' from storage.objects where bucket_id='models-01' and name='smoke-batch.glb') as batch_size; rollback;" '"legacy_size": *"1".*"batch_size": *"2"|"batch_size": *"2".*"legacy_size": *"1"'

echo "--- table privileges (TRUNCATE ignores RLS, so only the privilege protects the data; rolled back either way)"
must_fail "anon cannot TRUNCATE sensor_data" \
  "begin; set local role anon; truncate public.sensor_data; rollback;" "permission denied"
must_fail "authenticated cannot TRUNCATE artifacts" \
  "begin; set local role authenticated; truncate public.artifacts; rollback;" "permission denied"
must_fail "authenticated cannot TRUNCATE model_files" \
  "begin; set local role authenticated; truncate public.model_files; rollback;" "permission denied"

echo "--- model_files and RPCs"
ok_rows   "anon cannot attach a file to an artifact, and cannot read model_files" \
  "begin; $OBJ ('models-01','smoke-reg.glb','{\"size\":24,\"mimetype\":\"model/gltf-binary\"}'); do \$\$ declare a uuid; begin insert into public.artifacts(name) values ('smoke-a') returning id into a; set local role anon; perform public.register_model_file('models-01','smoke-reg.glb', a); if (select count(*) from public.model_files) <> 0 then raise exception 'anon can read model_files'; end if; reset role; if (select artifact_id from public.model_files where object_path='smoke-reg.glb') is not null then raise exception 'anon attached a file to an artifact'; end if; end \$\$; rollback;"
ok_rows   "a recorded file is never re-pointed to another artifact" \
  "begin; $OBJ ('models-01','smoke-repoint.glb','{\"size\":24}'); do \$\$ declare a uuid; b uuid; r uuid; begin insert into public.artifacts(name) values ('smoke-a') returning id into a; insert into public.artifacts(name) values ('smoke-b') returning id into b; perform public.register_model_file('models-01','smoke-repoint.glb', a); perform public.register_model_file('models-01','smoke-repoint.glb', b); select artifact_id into r from public.model_files where object_path='smoke-repoint.glb'; if r is distinct from a then raise exception 're-pointed'; end if; end \$\$; rollback;"
ok_rows   "anon can ask which batch to use" \
  "begin; set local role anon; select public.pick_model_bucket(1000) as bucket; rollback;" '"bucket": *"models-01"'
ok_rows   "a full first batch spills over to the second" \
  "begin; update public.model_buckets set capacity_bytes = 0 where bucket_id='models-01'; select public.pick_model_bucket(24) as bucket; rollback;" '"bucket": *"models-02"'
must_fail "a file over the per-file limit (200 MiB on the Pi) is refused" \
  "select public.pick_model_bucket(250000000);" "54000|larger than"
must_fail "the project cap stops pick_model_bucket" \
  "begin; update public.app_limits set value_bigint = 1 where key='storage_total_cap_bytes'; select public.pick_model_bucket(24); rollback;" "storage_full"
ok_rows   "a missing cap row falls back to the built-in default everywhere" \
  "begin; delete from public.app_limits where key='storage_total_cap_bytes'; select public.pick_model_bucket(24) as bucket, private.storage_under_cap() as under_cap; rollback;" '"bucket": *"models-01".*"under_cap": *true|"under_cap": *true.*"bucket": *"models-01"'
must_fail "anon cannot read usage numbers" \
  "begin; set local role anon; select * from public.model_storage_usage(); rollback;" "permission denied"
must_fail "anon cannot link files" \
  "begin; set local role anon; select public.link_model_files(); rollback;" "permission denied"

exit $fail
