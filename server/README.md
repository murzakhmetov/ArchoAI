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
