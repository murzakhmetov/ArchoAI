#  ArchoAI - AI-Powered Archaeological Conservation

ArchoAI is a comprehensive ecosystem designed for the digital preservation, analysis, and monitoring of ancient artifacts. It combines AI-driven damage detection, 3D reconstruction, and real-time environment monitoring into a single platform.

##  Project Overview

The project is divided into four main components:

1. ** Mobile Application (`archoai_app`)**: A Flutter-based mobile dashboard for archaeologists to visualize artifact data, monitor environmental conditions, and trigger AI analysis.
2. ** AI & Processing Scripts (`scripts/`)**: Python-based backend tools for:
   - **Damage Detection**: Identifying and measuring cracks on artifacts using DeepCrack models.
   - **3D Reconstruction**: Generating 3D models from images using the Tripo API (v3).
   - **Database Integration**: Synchronizing analysis results with Supabase.
3. ** IoT Monitoring (`monitoring.cpp`)**: ESP32-based firmware for real-time tracking of temperature, humidity, and air quality (VOC/Gas) in storage areas.
4. ** Landing Page (`landing_page/`)**: A modern web interface providing project information and vision.
5. ** Server on the Raspberry Pi (`server/`, `supabase/`)**: the database and the file storage run on the Pi itself (self-hosted Supabase stack in Docker), on a Supabase cloud project, or on both at once (see [Where it runs](#where-it-runs-cloud-raspberry-pi-or-both)). See [`server/README.md`](server/README.md).

---

##  Tech Stack

- **Mobile**: Flutter, Dart, Supabase-Flutter SDK
- **Backend/AI**: Python, PyTorch, OpenCV, Tripo API
- **Database**: self-hosted Supabase on the Raspberry Pi and/or a Supabase cloud project (PostgreSQL, Realtime, Storage)
- **Embedded**: C++ (Arduino/ESP32), DHT11, MQ-series Gas Sensors, OLED SSD1306
- **Web**: Vanilla JS, CSS (Modern Aurora Design)

---

##  Repository Structure

```tree
.
 archoai_app/          # Flutter mobile application
 landing_page/         # Project landing page
 scripts/              # AI inference and utility scripts
    main.py           # Main processing script
    withbutton.py     # Tripo 3D generation integration
    util/             # AI model utilities and networks
 monitoring.cpp        # ESP32 IoT Monitoring Firmware
 server/               # Raspberry Pi server: setup, systemd services, backups, system tests
 supabase/             # Database schema (migrations), storage buckets, policy tests
 README.md             # This file
```

---

##  Where it runs: cloud, Raspberry Pi, or both

| Setup | `archoai_app/.env` | Where the data is |
| --- | --- | --- |
| Pi only (default) | `SUPABASE_URL=http://<pi address>:54321` + `SUPABASE_KEY` (written by `server/setup.sh`) | on the Pi |
| Cloud only | `SUPABASE_URL=https://<ref>.supabase.co` (or `SUPABASE_PROJECT_REF=<ref>`) + the project's publishable key | in the cloud |
| Both | the Pi as above, plus `SUPABASE_FALLBACK_URL=https://<ref>.supabase.co` + `SUPABASE_FALLBACK_KEY` (the cloud's publishable key) | on both servers |

How "both" works:
- **One server at a time per client.** The app and the scanner scripts use the first server when it answers an authenticated request within 2.5 s, otherwise the second one (Pi on the local Wi-Fi, cloud everywhere else). `SUPABASE_SERVER=primary` or `fallback` forces one. In the app, **Profile → Server** (also on the sign-in screen) switches on the device.
- **Data is copied by the Pi.** Every client writes only to the server it uses. `server/replicate.py` on the Pi copies sensor readings, artifacts, photos and 3D models both ways every 2 minutes. It stays off until `server/replica.env` holds the cloud URL and the cloud's **secret** key (see [`server/README.md`](server/README.md)). Without it, the two servers are not kept in sync.
- **Accounts are per server.** Signing in on the Pi also signs in (or registers) on the cloud with the same e-mail and password, so a later switch to the cloud starts signed in; a cloud project that requires e-mail confirmation asks for it once. The other way round the app never sends a password in the background: the Pi's plain-HTTP address could belong to anybody on another network. After moving to the Pi, sign in there once.
- **The ESP32** posts to the one address in `monitoring.cpp`. With both servers, keep the Pi there.
- **Secret keys** never go into `archoai_app/.env`: it is bundled into the app. Only the replicator on the Pi holds them.

---

##  Setup & Installation

### AI Scripts
1. Navigate to the `scripts` folder.
2. Create a virtual environment: `python -m venv venv`.
3. Install dependencies: `pip install -r requirements.txt`.
4. Ensure your `pretrained_net_G.pth` model is in the root of the scripts folder.
5. Put your keys in `archoai_app/.env` (the scripts read it): `SUPABASE_URL` (the Pi server, `http://<pi address>:54321`; `server/setup.sh` writes it together with `SUPABASE_KEY`), `GEMINI_API_KEY`, `TRIPO_API_KEY` (see [Tripo API v3](#tripo-api-v3)) and `CAMERA_INDEX` (the webcam: `0` for a single USB camera, `python check_camera.py` lists them).
6. Run the unit tests (no network, no GPU needed): `python -m unittest discover -s tests -v` from `scripts/scripts`. `bash server/check_all.sh` checks the whole system on the Pi.

### Mobile App
1. Ensure Flutter is installed.
2. Copy `archoai_app/.env` from the Pi (`server/setup.sh` fills in the server address and key; add the Gemini and Tripo keys). Without the Pi, start from `archoai_app/.env.example`. The file is git-ignored but has to exist: it is declared as a Flutter asset, so `flutter run`, `flutter build` and `flutter test` all need it. The phone must be on the same Wi-Fi as the Pi, unless a cloud project is configured as the second server (see [Where it runs](#where-it-runs-cloud-raspberry-pi-or-both)).
3. Run `flutter pub get` inside `archoai_app`.
4. Run the app: `flutter run`.
5. Run the tests: `flutter test`.

> Values from `.env` are bundled into the app binary, so anyone with the app can extract them. A Supabase publishable key is meant for that; the Tripo and Gemini keys are secret and billable. Use keys with spending limits for demos and move those calls behind a backend (for example a Supabase Edge Function) before a public release.

### Turntable scanner (Arduino Uno + CNC Shield V3 + NEMA 17)
1. Connect the Arduino to the Pi by USB and the 12-24 V supply to the shield. Flash the firmware once: `bash turntable/flash.sh` (or open `turntable/turntable.ino` in the Arduino IDE).
2. Run `./venv/bin/python turntable_scan.py` in `scripts/scripts` and press **SPACE** (or **R**): the table turns 6 x 60 degrees with a photo at every stop, then DeepCrack marks the cracks, Tripo builds the 3D model (needs `TRIPO_API_KEY`) and Gemini fills in the artifact data. Everything (photos, 3D model, artifact row) is saved to the Pi server **and** to the Supabase cloud project (`SUPABASE_FALLBACK_URL` / `SUPABASE_FALLBACK_KEY` in `archoai_app/.env`, the same second server the app uses; both copies get the same artifact id, so `server/replicate.py` never duplicates them). On the Pi the 6 angle photos are listed in `artifacts.photo_urls`. **Q** quits.
3. Motion defaults (S-curve, 22 rpm, 0.3 s ramp, motor current off between moves) were picked by measuring the motor noise with the webcam microphone. In full-step mode software can only make it a little quieter; for a smooth, nearly silent turn put the three microstepping jumpers under the driver (power off) and set the steps below, or swap the driver for a TMC2208/TMC2209. `python turntable.py speed|ramp|steps ...` changes them; with microstepping jumpers under the driver set `steps 3200` (A4988) or `steps 6400` (DRV8825).

### Tripo API v3
The app (`archoai_app/lib/services/tripo_service.dart`) and the scripts (`scripts/scripts/tripo_client.py`) use **Tripo API v3**. The old `POST /v2/openapi/task` endpoint with a `type` field is gone; each task type has its own endpoint, `model` is a required parameter, and `file`/`files` became `input`/`inputs`.

| Step | v3 endpoint |
| --- | --- |
| Upload a photo | `POST /v3/files` (multipart `file`) returns `file_token` |
| One photo to 3D | `POST /v3/generation/image-to-model` with `input` + `model` |
| Several views to 3D | `POST /v3/generation/multiview-to-model` with `inputs` + `model` |
| Poll a task | `GET /v3/tasks/{task_id}` until `success`, read `output.model_url` |

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRIPO_API_KEY` | none | Tripo API key (Bearer token). |
| `TRIPO_MODEL` | `v3.1-20260211` | Geometry model. Also available: `v3.0-20250812`, `v2.5-20250123`. |
| `TRIPO_REGION` | `global` | `global` uses `https://openapi.tripo3d.ai/v3`, `china` uses `https://openapi.tripo3d.com/v3`. Accounts and keys belong to one region and the client never switches between them on its own. |
| `TRIPO_BASE_URL` | unset | Full base URL override, wins over `TRIPO_REGION`. |
| `TRIPO_FACE_LIMIT` | unset | Optional polycount cap. Smaller models are cheaper to load in the app viewer. |
| `TRIPO_GEOMETRY_QUALITY` | unset (standard) | `detailed` = Ultra mode, up to 2 M faces, +20 credits. |
| `TRIPO_TEXTURE_QUALITY` | unset (standard) | `detailed` (+10 credits), `extreme` (8K textures, very large files) or `fast`. |
| `TRIPO_PBR` | unset (true) | Physically based materials. |
| `TRIPO_TEXTURE_ALIGNMENT` | unset (`original_image`) | `original_image` keeps the textures true to the photos, `geometry` follows the shape. |
| `TRIPO_COMPRESS` | unset | `geometry` = meshopt compression. **Leave unset**: the app's 3D viewer (model_viewer_plus) cannot load meshopt-compressed models. |

**Best quality** (set in `archoai_app/.env` on the Pi): `TRIPO_MODEL=v3.1-20260211`, `TRIPO_GEOMETRY_QUALITY=detailed`, `TRIPO_TEXTURE_QUALITY=detailed`, `TRIPO_PBR=true`, `TRIPO_TEXTURE_ALIGNMENT=original_image` (no `TRIPO_COMPRESS`). The scripts and the app send these with every task. The Pi accepts model files up to 200 MiB (migration `20261128000002_pi_large_models.sql`); a Supabase Free-plan project still refuses files over 50 MiB, and such a model is then kept only on the Pi.

Behaviour worth knowing:
- Download links in a finished task are temporary. The app saves the GLB to the device as soon as the task succeeds, then copies it into the Supabase model batches and writes only the permanent public URL to `artifacts.model_url` (never the temporary Tripo link); the scripts download it immediately only when `UPLOAD_GLB_TO_SUPABASE=true`, which stores it in Supabase Storage and gives a permanent link. With `false`, or when that download fails, the temporary Tripo link is stored in the database as before and a warning is printed.
- Creating a task costs credits, so a create request is retried only when Tripo answers `429` (honouring `Retry-After`), never after a timeout or a server error.
- Reading a task and uploading or downloading files are retried with backoff on network errors, `429` and `5xx`.

### IoT Monitoring
The board measures five values; the app shows each of them with a 24 h chart (Dashboard: **Air** and **Soil**).

| Value | Sensor | ESP32 pin | Column |
| --- | --- | --- | --- |
| Air temperature, °C | DHT11 | GPIO26 | `temperature` |
| Air humidity, % | DHT11 | GPIO26 | `humidity` |
| Air quality, ppm (CO2 equivalent) | MQ-135 (AO, through a divider with a 5 V module) | GPIO34 | `air_ppm` (+ raw value in `air_quality`) |
| Soil moisture, % | capacitive soil moisture sensor v1.2 (3.3 V) | GPIO35 | `soil_moisture` |
| Soil temperature, °C | DS18B20 waterproof probe, 4.7 kOhm pull-up to 3.3 V | GPIO4 | `soil_temperature` |

1. Compile and flash `monitoring.cpp` using the Arduino IDE or PlatformIO (`bash server/esp32.sh upload` on the Pi).
2. Ensure you have the `Adafruit_SSD1306`, `DHT`, `OneWire`, `DallasTemperature` and `WiFi` libraries installed.
3. Calibrate once with the serial monitor (115200 baud): `SOIL_RAW_DRY` / `SOIL_RAW_WET` (raw value in dry air and in water) and `MQ135_R0_KOHM` (the "R0 for 420 ppm" value printed outdoors after the sensor has warmed up). A sensor that is not connected is sent as `null` and the app shows `--`.
4. Apply migration `supabase/migrations/20261128000003_soil_and_air_ppm.sql` on every server first (`supabase migration up` on the Pi); until then the server refuses the new fields.
5. `SUPABASE_URL` and `SUPABASE_KEY` in the source point at the Pi server. Set `WIFI_SSID` / `WIFI_PASSWORD` to the Wi-Fi network the Pi is on.

---

##  Database Schema (Supabase on the Raspberry Pi)

The whole backend lives in [`supabase/`](supabase/README.md) as plain SQL migrations plus `config.toml`, so no hand-written `create table` is needed:

- **Tables:** `artifacts` (`id`, `name`, `image_path`, `model_url`, `local_model_path`, `created_at`, `updated_at`, `status`, `type`, `material`, `era`, `purpose`, `condition`, `crack_percentage`, `tripo_task_id`, `model_file_id`) and `sensor_data` (`id`, `temperature` and `humidity` of the air, `air_quality` (raw MQ-135 value), `air_ppm`, `soil_moisture`, `soil_temperature`, `created_at`), with row level security and realtime; `artifacts` has CHECK constraints, and the value ranges of `sensor_data` are enforced by an insert policy. The first migration, `20260101000000_baseline_tables.sql`, creates the original tables on a fresh project.
- **Model batches:** buckets `models-01` to `models-04` (public, 50 MB per file, `.glb` only) are filled in order. `pick_model_bucket(size)` returns the first batch with room, `register_model_file()` records each file in `model_files`, and a global cap (20 GB on the Pi, enforced by storage policies for every bucket) stops uploads before the SD card is full.
- **Buckets do not add capacity.** The batches are a logical split of the cap. For more room (a larger card), raise `app_limits.storage_total_cap_bytes` and the capacities in `model_buckets`.
- **Where it runs:** on the Raspberry Pi (`server/README.md`): `bash server/setup.sh` starts the stack, applies every file of `supabase/migrations/` and creates the buckets from `config.toml`; a new migration is applied with `supabase migration up`. On the Pi the storage cap is 20 GB (migration `20261126000001_pi_local_server.sql`) instead of the cloud's 900 MB. `bash supabase/tests/smoke.sh` (33 policy checks) and `python3 supabase/tests/migration_drift.py` run against the Pi by default; `REMOTE=<ref>` / `--ref <ref>` still target a cloud project. Details and the list of open security decisions are in [`supabase/README.md`](supabase/README.md).

---

##  License

This project is developed for archaeological research and conservation purposes. All rights reserved.
