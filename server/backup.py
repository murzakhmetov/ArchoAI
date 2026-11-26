#!/usr/bin/env python3
"""Back up the ArchoAI server on this Pi, and restore such a backup.

    python3 server/backup.py create [--dest DIR]       # default: ~/archoai-backups/<date-time>/
    python3 server/backup.py restore DIR [--force]     # into the running server

A backup folder holds:
  data.sql    the rows of public.artifacts, public.sensor_data, public.model_files and the app
              accounts (auth.users, auth.identities; passwords are bcrypt hashes);
  files/      every stored file, as files/<bucket>/<path>;
  files.json  the list of files with content type, size and SHA-256.
The limits (app_limits, model_buckets) come from the migrations and are not part of it.

Restore is meant for a fresh server (server/setup.sh on a new card): it refuses when the server
already has rows, unless --force. The rows are loaded in one transaction, then the files are
uploaded again through the Storage API.

The SD card is the weak spot of a Pi: copy the backup folders to another disk or computer.
"""
import argparse
import datetime
import hashlib
import json
import pathlib
import subprocess
import sys
import urllib.parse

import requests

REPO = pathlib.Path(__file__).resolve().parents[1]
DB_CONTAINER = "supabase_db_ArchoAI"
LOCAL_API = "http://127.0.0.1:54331"
TABLES = ("public.artifacts", "public.sensor_data", "public.model_files", "auth.users", "auth.identities")
COUNTS = ("select row_to_json(c) from (select (select count(*) from public.artifacts) as artifacts, "
          "(select count(*) from public.sensor_data) as sensor_data, "
          "(select count(*) from public.model_files) as model_files, "
          "(select count(*) from auth.users) as users) c;")


def secret_key():
    for line in (REPO / "supabase" / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("ARCHOAI_SECRET_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit("supabase/.env has no ARCHOAI_SECRET_KEY: run server/setup.sh first")


def psql(sql, *, single_transaction=False):
    command = ["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", "supabase_admin", "-d", "postgres",
               "-v", "ON_ERROR_STOP=1", "-q", "-At", "-f", "-"]
    if single_transaction:
        command.insert(-2, "--single-transaction")
    done = subprocess.run(command, input=sql, capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        sys.exit(f"database: {done.stderr.strip()[-1500:]}")
    return done.stdout


def counts():
    return json.loads(psql(COUNTS).strip().splitlines()[-1])


def headers(key, **extra):
    return {"apikey": key, "Authorization": f"Bearer {key}", **extra}


def create(dest):
    key = secret_key()
    dest.mkdir(parents=True, exist_ok=False)
    dest.chmod(0o700)  # holds password hashes

    command = ["docker", "exec", DB_CONTAINER, "pg_dump", "-U", "supabase_admin", "-d", "postgres",
               "--data-only", "--column-inserts", "--rows-per-insert=1000", "--no-owner", "--no-privileges"]
    for table in TABLES:
        command += ["-t", table]
    done = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if done.returncode != 0:
        sys.exit(f"pg_dump failed: {done.stderr.strip()[-800:]}")
    dump = "\n".join(line for line in done.stdout.splitlines() if not line.startswith("\\"))  # no psql meta-commands
    (dest / "data.sql").write_text("SET session_replication_role = replica;\n" + dump + "\nRESET session_replication_role;\n",
                                   encoding="utf-8")

    objects = json.loads(psql(
        "select coalesce(json_agg(json_build_object('bucket', bucket_id, 'name', name, "
        "'mimetype', coalesce(metadata->>'mimetype', 'application/octet-stream')) order by bucket_id, name), '[]') "
        "from storage.objects;").strip().splitlines()[-1])
    session = requests.Session()
    manifest = []
    for obj in objects:
        response = session.get(f"{LOCAL_API}/storage/v1/object/{obj['bucket']}/{urllib.parse.quote(obj['name'])}",
                               headers=headers(key), timeout=300)
        if response.status_code != 200:
            sys.exit(f"download of {obj['bucket']}/{obj['name']} failed: HTTP {response.status_code}")
        target = dest / "files" / obj["bucket"] / obj["name"]
        if dest.resolve() not in target.resolve().parents:
            sys.exit(f"refusing an object name that leaves the backup folder: {obj['name']!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(response.content)
        manifest.append(dict(obj, size=len(response.content), sha256=hashlib.sha256(response.content).hexdigest()))
    (dest / "files.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    rows = counts()
    (dest / "counts.json").write_text(json.dumps(rows), encoding="utf-8")
    print(f"backup written to {dest}: rows {rows}, files {len(manifest)}")


def restore(source, force):
    key = secret_key()
    expected = json.loads((source / "counts.json").read_text(encoding="utf-8"))
    before = counts()
    if any(before.values()) and not force:
        sys.exit(f"the server already has rows {before}; restore is meant for a fresh server (--force to load anyway)")
    psql((source / "data.sql").read_text(encoding="utf-8"), single_transaction=True)
    after = counts()
    print(f"rows restored: {{{', '.join(f'{k}: {after[k] - before[k]}' for k in after)}}} (backup had {expected})")

    session = requests.Session()
    manifest = json.loads((source / "files.json").read_text(encoding="utf-8"))
    for obj in manifest:
        data = (source / "files" / obj["bucket"] / obj["name"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != obj["sha256"]:
            sys.exit(f"{obj['bucket']}/{obj['name']} in the backup is damaged (SHA-256 differs)")
        response = session.post(f"{LOCAL_API}/storage/v1/object/{obj['bucket']}/{urllib.parse.quote(obj['name'])}",
                                data=data, timeout=300,
                                headers=headers(key, **{"Content-Type": obj["mimetype"], "x-upsert": "true"}))
        if response.status_code not in (200, 201):
            sys.exit(f"upload of {obj['bucket']}/{obj['name']} failed: {response.status_code} {response.text[:200]}")
    print(f"files restored: {len(manifest)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("create", help="write a new backup folder")
    make.add_argument("--dest", type=pathlib.Path,
                      default=pathlib.Path.home() / "archoai-backups" / datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S"))
    back = sub.add_parser("restore", help="load a backup folder into the running server")
    back.add_argument("source", type=pathlib.Path)
    back.add_argument("--force", action="store_true", help="load although the server already has rows")
    args = parser.parse_args()
    if args.command == "create":
        create(args.dest)
    else:
        restore(args.source, args.force)


if __name__ == "__main__":
    main()
