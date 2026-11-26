# ArchoAI server on the Raspberry Pi

The database and the file storage of ArchoAI run on the Raspberry Pi itself. They replace the
Supabase cloud project (`wvhdhfddtusppjmsgmvn`, which is no longer used). The Pi runs the
open-source Supabase stack in Docker, so the mobile app, the ESP32 board and the scanner scripts
speak exactly the same API as before; only the address and the key changed.

| Part | Where |
| --- | --- |
| API for phones, the ESP32 and the scripts | `http://<pi address>:54321` (this Pi: `http://192.168.1.61:54321`) |
| Database (PostgreSQL 17) | Docker volume `supabase_db_ArchoAI`, port 54322 on 127.0.0.1 only |
| Files (photos, 3D models) | Docker volume `supabase_storage_ArchoAI`, served under `/storage/v1/object/public/...` |
| Services | auth (accounts), REST, storage, realtime (live dashboard), API gateway |
| Secrets | `supabase/.env` (git-ignored): JWT secret, publishable key, secret key |
| Client settings | `archoai_app/.env` (git-ignored): `SUPABASE_URL`, `SUPABASE_KEY`, `GEMINI_API_KEY`, ... |

## How it is wired

```
phone app ─┐                     ┌─ 127.0.0.1:54331  API gateway (Kong) ── auth, REST, storage, realtime
ESP32 ─────┼─► <pi>:54321 ──────►│
scripts ───┘   archoai-api-lan   └─ 127.0.0.1:54322  PostgreSQL (never reachable from the network)
               (socat forwarder)
```

* Docker publishes every container port on **127.0.0.1 only**: the stack runs on the Docker network
  `archoai_supabase`, created with `com.docker.network.bridge.host_binding_ipv4=127.0.0.1`
  (`/etc/docker/daemon.json` does the same for the default network).
* `archoai-api-lan.service` forwards port **54321** of all network interfaces to the gateway. Nothing
  else is reachable from the Wi-Fi, in particular not the database.
* The stack signs its own tokens (`supabase/.env`). The demo keys that every Supabase CLI install
  knows are not accepted. The S3 protocol of the storage is switched off.

## Setup (already done on this Pi)

```bash
bash server/setup.sh               # idempotent: secrets, Docker network, systemd services, app .env
python3 server/import_from_cloud.py   # once: copies the cloud data (rows, accounts, files) to the Pi
```

`setup.sh` needs Docker (`docker.io`), the Supabase CLI (`.deb` from the GitHub releases), `socat`
and Python 3. The first start downloads about 2.5 GB of images. The import needs `supabase login`
and reads the cloud project only; on 2026-11-26 it copied 66 artifacts, 144 sensor readings, 4
accounts and 74 photos. Users keep their passwords and sign in once more.

## Daily use

```bash
systemctl status archoai-supabase archoai-api-lan     # both start at boot
sudo systemctl restart archoai-supabase               # restart the stack (data is kept)
supabase status                                       # addresses (the gateway shows as 54331)
```

* **Never** run `supabase stop --no-backup` or `supabase db reset`: both delete the database.
  Plain `supabase start` without `--network-id archoai_supabase` would publish the ports on all
  interfaces; use the service instead.
* New migration file: `supabase migration up` applies it to the running database.
* Browse the data: `supabase db query "select ..."` on the Pi, or open the app.

## The clients

* **Mobile app:** `archoai_app/.env` must contain `SUPABASE_URL=http://<pi address>:54321` and the
  Pi's `SUPABASE_KEY` (setup.sh writes both). The file is bundled into the app at build time and is
  git-ignored, so copy it to the computer that builds the APK (`scp parallax@<pi>:ArchoAI/archoai_app/.env archoai_app/`).
  Phones must be on the same Wi-Fi as the Pi. Plain `http` is already allowed by the app
  (Android `usesCleartextTraffic`, iOS `NSAllowsArbitraryLoads`).
* **ESP32 (`monitoring.cpp`):** `SUPABASE_URL` and `SUPABASE_KEY` point at the Pi. The board must
  join the same Wi-Fi network as the Pi (`WIFI_SSID` / `WIFI_PASSWORD`). Flash it from the Pi: plug the
  board into a USB port and run `bash server/esp32.sh upload` (`build` only compiles, `monitor` shows
  its serial output). The Arduino IDE on a PC works as before.
* **Scanner scripts:** read `archoai_app/.env`. `SUPABASE_URL` must be the Pi's network address, not
  127.0.0.1: the photo and model links stored in the database must open on the phones.
  `CAMERA_INDEX` selects the webcam (`0` for the single USB camera; `check_camera.py` lists them).

**Give the Pi a fixed address** (a DHCP reservation in the router). If the address changes anyway:
run `bash server/setup.sh` (updates the app `.env`), change `monitoring.cpp`, rebuild the app, and
rewrite the stored links:

```bash
supabase db query "update public.artifacts set image_path = replace(image_path, 'http://OLD:54321', 'http://NEW:54321'), model_url = replace(model_url, 'http://OLD:54321', 'http://NEW:54321')"
```

## Storage limits

The cloud's 1 GB Free-plan quota does not apply on the Pi. Migration
`20261126000001_pi_local_server.sql` sets the cap to 20 GB for all files together (4 model batches
of 5 GB). Check the card with `df -h /` and lower the cap when it gets full:
`update public.app_limits set value_bigint = <bytes> where key = 'storage_total_cap_bytes';`.

## Backups

The SD card is the weak point of a Pi. Make backups and copy them to another disk or computer:

```bash
python3 server/backup.py create                       # -> ~/archoai-backups/<date-time>/
python3 server/backup.py restore ~/archoai-backups/<folder>   # into a fresh server (after setup.sh)
```

A backup holds the rows (artifacts, sensor data, model files, accounts) and every stored file with
its SHA-256. A daily backup, for example: `crontab -e` and add
`30 3 * * * python3 /home/parallax/ArchoAI/server/backup.py create`.

## Cloud, Pi, or both

ArchoAI runs in one of three deployments. The clients (app, scanner scripts, ESP32) always talk to
**one** server; what changes is which one, and whether a second server is kept in step.

| Deployment | Clients use | Replication |
| --- | --- | --- |
| **Cloud only** | the Supabase cloud project (`SUPABASE_URL` of the project, or `SUPABASE_PROJECT_REF`) | none; no Pi needed |
| **Pi only** (the setup above) | the Pi, `http://<pi address>:54321` | none; leave `server/replica.env` out |
| **Both** | the Pi while it is reachable, otherwise the cloud | `server/replicate.py` on the Pi copies the data both ways |

A Pi that does not have `server/replica.env` behaves exactly as described above: the replicator
prints `replication not configured (server/replica.env)` and does nothing.

### Switch replication on

On the Pi, in the checked-out repository:

```bash
cp server/replica.env.example server/replica.env
nano server/replica.env           # REPLICA_URL, REPLICA_SECRET_KEY, REPLICA_LOCAL_PUBLIC_URL
python3 server/replicate.py --dry-run   # shows what would be written, writes nothing
bash server/setup.sh              # installs archoai-replicate.service + .timer (idempotent)
sudo systemctl start archoai-replicate.service   # first run now, then the timer repeats it
journalctl -u archoai-replicate -n 30     # what the last runs did
```

* `REPLICA_URL` is the cloud project address, `REPLICA_SECRET_KEY` its **secret** key (Dashboard,
  Project Settings, API Keys). Put them only into `server/replica.env`: the file is git-ignored,
  and the secret key must never be written into `archoai_app/.env` (that file is bundled into the app).
* `REPLICA_LOCAL_PUBLIC_URL` is the Pi address that phones use (the `SUPABASE_URL` of
  `archoai_app/.env`). It is written into the photo and model links of artifacts copied to the Pi.
  If it is left out, the replicator takes the address that the Pi's existing artifacts already use.
* The Pi side uses `ARCHOAI_SECRET_KEY` of `supabase/.env`, as `backup.py` does.
* `archoai-replicate.timer` runs the replicator 2 minutes after boot and then every 2 minutes. The
  service has `ConditionPathExists=server/replica.env`, so it stays inert until the file exists. If
  the Pi has no internet connection the run fails (exit code 2, visible in `systemctl status`) and
  the next run tries again.
* The replicator needs only the Pi's `python3` (standard library); nothing to install.
  The state (watermarks, and the readings copied into each server in the last scan window) is kept
  by the service in `/var/lib/archoai/replica_state.json` (systemd `StateDirectory`; it is written
  only when it changed). A manual run uses the same state and the same lock (the lock file sits
  next to it) whenever `/var/lib/archoai` exists and is writable for you, so it cannot overlap
  with the timer; otherwise it falls back to `server/.replica_state.json`. The recommended manual
  run is `sudo systemctl start archoai-replicate.service`. For `--full` or `--dry-run` use the
  shared state: `sudo -u <service user> python3 server/replicate.py --full` (the user of
  `archoai-replicate.service`, normally the one that ran `setup.sh`), or add
  `STATE_DIRECTORY=/var/lib/archoai` if you run it as that user from another shell.
  `REPLICA_STATE_FILE` sets any other file. If the state is lost, the next run rescans everything
  and copies only what is missing, but readings that were deleted on their origin can come back.
  **`--full` has the same effect**: it rescans all readings, and a reading that was deleted on its
  origin before the guard horizon (the watermark minus 1 hour) is copied back from the other server.
  The run prints a warning when `--full` is used.

### What is replicated

* **Sensor readings** (`sensor_data`), both ways. A reading missing on the other server is inserted
  without `id` (the target numbers it) and with its original `created_at`. Two readings are the same
  when time (to the microsecond, UTC), temperature, humidity and air quality are equal. A reading
  that the replicator copied into a server is never copied back out of it (it remembers the copied
  readings for as long as a rescan can reach them: those not older than the watermark minus 1 hour),
  so a reading deleted where it was written is not brought back, however long the pause was. The readings of the app's end-to-end test (humidity
  33.25 and air quality 3999) are skipped.
* **Artifacts**, both ways, matched by `id`. A missing artifact is copied with its photo and 3D
  model (same bucket and path; links are rewritten to the target server's address). If both servers
  have the artifact and the content differs, the version with the newer `updated_at` overwrites the
  other (the Pi wins a tie). Identical content is never rewritten: the database bumps `updated_at`
  on every update, so comparing timestamps alone would make the servers rewrite each other forever.
  `local_model_path` (a path on one device) and `model_file_id` (a row of one server) are not
  compared and not copied.
* **Models and quota.** The replicator first asks the target's `pick_model_bucket(size)` whether the
  target has room (the secret key bypasses the storage policies, so this is the only check of the
  cap). The model goes to the batch the function names, else to the same bucket. The cloud allows
  only 1 GB in total (the Pi 20 GB): if the target has no room, the model is **skipped**, the
  artifact row is still copied and keeps pointing at the source server's file, and the run
  succeeds. Every later run tries again (nothing is written until the file fits) and then rewrites
  the link. Without the function (HTTP 404, `PGRST202`) there is no cap check; any other failure of
  the function (permission, 5xx) skips the artifact until the next run. Copied models are
  recorded in the target's `model_files`. Photos are copied without a quota check.
* **Changed files.** When an artifact row is written, its photo and model are compared with the
  other server's copy (size, and etag only when both are md5 values; other etags say nothing). A
  different file replaces the target's only when the source row won, the row update went through,
  and both servers report `Last-Modified` with the source's file being newer. If a timestamp is
  missing or the target's file is newer, the target's file is kept and the run logs it: the
  replicator never destroys the newer file. A file replaced without any change of the row is not noticed.
* **Failures.** A server hiccup (5xx, 408, 429) or one network failure while copying files skips that
  artifact for this run (retried in 2 minutes); three network failures in a row end the run.
  Uploads are sent in blocks, so the 120 s timeout applies per block, not to a whole 50 MB file.
  An artifact changed on the target between reading and writing is left for the next run. If more
  than 5 artifacts in one run were skipped because of network failures, the run ends with exit
  code 2 (visible in `systemctl status`).
* **Configuration errors.** A 404 without a table error (a wrong `REPLICA_URL`, path `PGRST125`,
  or something that is not PostgREST) and a server with neither table end the run with exit code 2
  and "check REPLICA_URL". A single missing table only skips that part.
* **Readings a server refuses for good** (a 4xx other than 408/429, for example a CHECK violation)
  are logged once, remembered (at most 1000 per server, within the scan window) and skipped from then
  on, so they do not hold the watermark back.
  Only links to the buckets `artifact-images`, `3d1` and `models-NN` with a normal path are followed;
  other links are copied as plain text and never requested.

### What is not replicated

* **Deletions.** Nothing is ever deleted on either server; an artifact deleted on one server
  comes back from the other one. Delete it on both.
* App accounts (`auth.users`): each server has its own; users sign in once on each.
* Files that no artifact points to, the tables `app_limits` and `model_buckets` (each server keeps
  its own limits), and the schema (migrations are applied to each server separately).
* Readings written with an old `created_at` after the watermark has passed it (a backfill). Run
  `python3 server/replicate.py --full` once after such a backfill.

Both servers need the tables, functions and buckets of `supabase/migrations` (the replicator uses
`artifacts`, `sensor_data`, `model_files`, `pick_model_bucket` and the buckets of `supabase/config.toml`).
The migration `20261126000001_pi_local_server.sql` is for the Pi only and must not be applied to the cloud project.

## Tests

```bash
bash server/check_all.sh            # the whole system, about 10 minutes
QUICK=1 bash server/check_all.sh    # without the app end-to-end run, the web build and the ESP32 build
```

| Test | What it proves |
| --- | --- |
| `server/tests/test_server_api.py` | The API contracts of all clients against the live server: ESP32 readings (ranges, rate limit), scanner uploads and `store_model`, app sign-up / sign-in / updates / model upload / RPCs, realtime delivery; only port 54321 is reachable from the network; demo keys get no more than anonymous access |
| `server/tests/test_scan_pipeline.py` | `withbutton.py` and `withbutton_manual.py` scan end to end (DeepCrack, storage, database real; camera, Tripo and Gemini faked by `scan_harness.py`) |
| `server/tests/run_app_e2e.sh` | The real Flutter app (Linux build of the same code) registers, shows a reading live on the dashboard and lists the collection from the Pi |
| `server/tests/test_crack_api.py` | The crack detection API used by the web UI |
| `server/tests/test_replicate.py` | The replicator against in-memory fakes of both servers: inert without config, readings and artifacts both ways, no duplicates, a second run writes nothing, deleted readings stay deleted, newer version wins, models refused by a full target are skipped and finished later, changed files, timeouts, races, traversal, `--dry-run` writes nothing |
| `server/tests/test_backup.py` | A backup holds every row and file, and its SQL rebuilds every row (checked in a rolled-back transaction) |
| `server/esp32.sh build` | The ESP32 firmware compiles (PlatformIO, ESP32 DevKit) |
| `supabase/tests/smoke.sh`, `migration_drift.py` | Database policies (33 checks), and that the migrations describe the database exactly (298 facts) |

Every test removes what it creates. The live data is never changed.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| App: "Could not initialize" / no data | Phone on the same Wi-Fi? `curl http://<pi>:54321/auth/v1/health -H "apikey: <SUPABASE_KEY>"` from another computer |
| ESP32 shows `HTTP -1` | Board on the Pi's Wi-Fi? Address in `monitoring.cpp` current? |
| `supabase status` permission denied | Log out and in again once (the user was added to the `docker` group) |
| Everything down after a reboot | `systemctl status archoai-supabase` and `journalctl -u archoai-supabase` |
