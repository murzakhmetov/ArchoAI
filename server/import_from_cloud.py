#!/usr/bin/env python3
"""Copy the data of the old Supabase cloud project into the ArchoAI server on this Pi (run once).

Copied:
  * public.artifacts, public.sensor_data and public.model_files, ids and timestamps kept;
  * the app accounts (auth.users, auth.identities): passwords are bcrypt hashes and keep working.
    Sessions are not copied, so every user signs in once more;
  * every file of the storage buckets, downloaded through its public link and uploaded to the Pi;
  * links in artifacts.image_path / model_url are rewritten from https://<ref>.supabase.co to the
    Pi's address (SUPABASE_URL in archoai_app/.env).
Nothing is written to the cloud project: read-only queries and public downloads only.
The configuration tables (app_limits, model_buckets) keep the Pi's own values.

Needs `supabase login` (once) and the running Pi server (server/setup.sh). The database part runs
in one transaction: it is either imported completely or not at all. Run from anywhere:

    python3 server/import_from_cloud.py [--ref <project ref>] [--force]

--force imports although the Pi already has rows; rows with the same id make the import fail.
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import urllib.parse

import requests

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_REF = "wvhdhfddtusppjmsgmvn"
DB_CONTAINER = "supabase_db_ArchoAI"
LOCAL_API = "http://127.0.0.1:54331"  # [api] port of supabase/config.toml
WANTED = {"public.artifacts", "public.sensor_data", "public.model_files", "auth.users", "auth.identities"}
COUNTS = ("select (select count(*) from public.artifacts) as artifacts, "
          "(select count(*) from public.sensor_data) as sensor_data, "
          "(select count(*) from public.model_files) as model_files, "
          "(select count(*) from auth.users) as users")


def env_file(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def cloud_query(ref, sql):
    """Rows of a read-only query against the cloud project (supabase CLI, logged in)."""
    done = subprocess.run(["supabase", "db", "query", f"--remote={ref}", "--read-only", "-o", "json", sql],
                          cwd=REPO, capture_output=True, text=True, timeout=300)
    out = done.stdout
    start = out.find("{")
    if done.returncode != 0 or start < 0:
        sys.exit(f"cloud query failed: {(done.stderr or out).strip()[-500:]}")
    return json.loads(out[start:])["rows"]


def local_sql(sql, *, single_transaction=False):
    """Runs SQL on the Pi as supabase_admin (superuser) inside the database container."""
    command = ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", "supabase_admin", "-d", "postgres",
               "-v", "ON_ERROR_STOP=1", "-q", "-At", "-f", "-"]
    if single_transaction:
        command.insert(-2, "--single-transaction")
    done = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=600)
    if done.returncode != 0:
        sys.exit(f"local database: {done.stderr.strip()[-1500:]}")
    return done.stdout


def local_counts():
    line = local_sql(f"select row_to_json(c) from ({COUNTS}) c;").strip().splitlines()[-1]
    return json.loads(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ref", default=DEFAULT_REF, help="cloud project ref")
    parser.add_argument("--force", action="store_true", help="import although the Pi already has data")
    args = parser.parse_args()

    app_env = env_file(REPO / "archoai_app" / ".env")
    secret = env_file(REPO / "supabase" / ".env").get("ARCHOAI_SECRET_KEY")
    pi_url = (app_env.get("SUPABASE_URL") or "").rstrip("/")
    if not pi_url or not secret:
        sys.exit("Run server/setup.sh first: SUPABASE_URL (archoai_app/.env) or the secret key is missing.")
    cloud_url = f"https://{args.ref}.supabase.co"

    before = local_counts()
    if any(before.values()) and not args.force:
        sys.exit(f"The Pi already has data {before}; nothing imported (use --force to import anyway).")

    # ---------------------------------------------------------------- database rows
    tables = [row["t"] for row in cloud_query(args.ref, (
        "select table_schema || '.' || table_name as t from information_schema.tables "
        "where table_schema in ('public', 'auth') and table_type = 'BASE TABLE'"))]
    missing = WANTED - set(tables)
    if missing:
        sys.exit(f"cloud project lacks {sorted(missing)}")
    excluded = sorted(set(tables) - WANTED)
    expected = cloud_query(args.ref, COUNTS)[0]
    print(f"cloud project {args.ref}: {expected}")

    with tempfile.TemporaryDirectory(prefix="archoai-import-") as tmp:
        os.chmod(tmp, 0o700)  # the dump holds password hashes
        dump = pathlib.Path(tmp) / "data.sql"
        done = subprocess.run(["supabase", "db", "dump", f"--remote={args.ref}", "--data-only",
                               "--schema", "public,auth", "-x", ",".join(excluded), "-f", str(dump)],
                              cwd=REPO, capture_output=True, text=True, timeout=600)
        if done.returncode != 0 or not dump.exists():
            sys.exit(f"supabase db dump failed: {done.stderr.strip()[-800:]}")
        sql = dump.read_text(encoding="utf-8")
        if "session_replication_role" not in sql:
            sys.exit("unexpected dump format (no session_replication_role); nothing imported")
        # Links to the cloud storage now point at the Pi. Same transaction, triggers still off, so
        # updated_at keeps its original value.
        quote = lambda text: "'" + text.replace("'", "''") + "'"  # noqa: E731
        rewrite = (
            f"update public.artifacts set image_path = {quote(pi_url)} || substr(image_path, {len(cloud_url) + 1}) "
            f"where image_path like {quote(cloud_url + '/%')};\n"
            f"update public.artifacts set model_url = {quote(pi_url)} || substr(model_url, {len(cloud_url) + 1}) "
            f"where model_url like {quote(cloud_url + '/%')};\n"
        )
        body, reset = sql.rsplit("RESET ALL;", 1) if "RESET ALL;" in sql else (sql, "")
        local_sql(body + rewrite + "RESET ALL;" + reset, single_transaction=True)

    after = local_counts()
    imported = {key: after[key] - before[key] for key in after}
    print(f"imported rows: {imported}")
    if imported != expected:
        sys.exit(f"row counts differ from the cloud project: expected {expected}")

    # ---------------------------------------------------------------- storage files
    objects = cloud_query(args.ref, (
        "select bucket_id, name, coalesce(metadata->>'mimetype', 'application/octet-stream') as mimetype, "
        "(metadata->>'size')::bigint as size from storage.objects "
        "where name not like '%.emptyFolderPlaceholder' order by bucket_id, name"))
    copied = 0
    session = requests.Session()
    for obj in objects:
        path = urllib.parse.quote(obj["name"])
        source = session.get(f"{cloud_url}/storage/v1/object/public/{obj['bucket_id']}/{path}", timeout=120)
        if source.status_code != 200 or (obj["size"] is not None and len(source.content) != obj["size"]):
            print(f"  skipped {obj['bucket_id']}/{obj['name']}: download HTTP {source.status_code}")
            continue
        target = session.post(f"{LOCAL_API}/storage/v1/object/{obj['bucket_id']}/{path}", data=source.content,
                              timeout=120, headers={"apikey": secret, "Authorization": f"Bearer {secret}",
                                                    "Content-Type": obj["mimetype"], "x-upsert": "true"})
        if target.status_code not in (200, 201):
            sys.exit(f"upload of {obj['bucket_id']}/{obj['name']} to the Pi failed: {target.status_code} {target.text[:300]}")
        copied += 1
    print(f"copied files: {copied} of {len(objects)}")

    broken = local_sql(
        "select count(*) from public.artifacts a where a.model_url like '%/storage/v1/object/public/%' "
        "and not exists (select 1 from storage.objects o where a.model_url like "
        "'%/storage/v1/object/public/' || o.bucket_id || '/' || o.name);").strip()
    if broken and broken != "0":
        print(f"note: {broken} artifact model links point to files that did not exist in the cloud either")
    print("done: the Pi now holds the cloud data")


if __name__ == "__main__":
    main()
