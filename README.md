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
5. ** Server on the Raspberry Pi (`server/`, `supabase/`)**: the database and the file storage run on the Pi itself (self-hosted Supabase stack in Docker) and replace the Supabase cloud project. See [`server/README.md`](server/README.md).

---

##  Tech Stack

- **Mobile**: Flutter, Dart, Supabase-Flutter SDK
- **Backend/AI**: Python, PyTorch, OpenCV, Tripo API
- **Database**: self-hosted Supabase on the Raspberry Pi (PostgreSQL, Realtime, Storage)
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
2. Copy `archoai_app/.env` from the Pi (`server/setup.sh` fills in the server address and key; add the Gemini and Tripo keys). Without the Pi, start from `archoai_app/.env.example`. The file is git-ignored but has to exist: it is declared as a Flutter asset, so `flutter run`, `flutter build` and `flutter test` all need it. The phone must be on the same Wi-Fi as the Pi.
3. Run `flutter pub get` inside `archoai_app`.
4. Run the app: `flutter run`.
5. Run the tests: `flutter test`.

> Values from `.env` are bundled into the app binary, so anyone with the app can extract them. A Supabase publishable key is meant for that; the Tripo and Gemini keys are secret and billable. Use keys with spending limits for demos and move those calls behind a backend (for example a Supabase Edge Function) before a public release.

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

Behaviour worth knowing:
- Download links in a finished task are temporary. The app saves the GLB to the device as soon as the task succeeds, then copies it into the Supabase model batches and writes only the permanent public URL to `artifacts.model_url` (never the temporary Tripo link); the scripts download it immediately only when `UPLOAD_GLB_TO_SUPABASE=true`, which stores it in Supabase Storage and gives a permanent link. With `false`, or when that download fails, the temporary Tripo link is stored in the database as before and a warning is printed.
- Creating a task costs credits, so a create request is retried only when Tripo answers `429` (honouring `Retry-After`), never after a timeout or a server error.
- Reading a task and uploading or downloading files are retried with backoff on network errors, `429` and `5xx`.

### IoT Monitoring
1. Compile and flash `monitoring.cpp` using the Arduino IDE or PlatformIO.
2. Ensure you have the `Adafruit_SSD1306`, `DHT`, and `WiFi` libraries installed.
3. `SUPABASE_URL` and `SUPABASE_KEY` in the source point at the Pi server. Set `WIFI_SSID` / `WIFI_PASSWORD` to the Wi-Fi network the Pi is on.

---

##  Database Schema (Supabase on the Raspberry Pi)

The whole backend lives in [`supabase/`](supabase/README.md) as plain SQL migrations plus `config.toml`, so no hand-written `create table` is needed:

- **Tables:** `artifacts` (`id`, `name`, `image_path`, `model_url`, `local_model_path`, `created_at`, `updated_at`, `status`, `type`, `material`, `era`, `purpose`, `condition`, `crack_percentage`, `tripo_task_id`, `model_file_id`) and `sensor_data` (`id`, `temperature`, `humidity`, `air_quality`, `created_at`), with row level security and realtime; `artifacts` has CHECK constraints, and the value ranges of `sensor_data` are enforced by an insert policy. The first migration, `20260101000000_baseline_tables.sql`, creates the original tables on a fresh project.
- **Model batches:** buckets `models-01` to `models-04` (public, 50 MB per file, `.glb` only) are filled in order. `pick_model_bucket(size)` returns the first batch with room, `register_model_file()` records each file in `model_files`, and a global cap (20 GB on the Pi, enforced by storage policies for every bucket) stops uploads before the SD card is full.
- **Buckets do not add capacity.** The batches are a logical split of the cap. For more room (a larger card), raise `app_limits.storage_total_cap_bytes` and the capacities in `model_buckets`.
- **Where it runs:** on the Raspberry Pi (`server/README.md`): `bash server/setup.sh` starts the stack, applies every file of `supabase/migrations/` and creates the buckets from `config.toml`; a new migration is applied with `supabase migration up`. On the Pi the storage cap is 20 GB (migration `20261126000001_pi_local_server.sql`) instead of the cloud's 900 MB. `bash supabase/tests/smoke.sh` (33 policy checks) and `python3 supabase/tests/migration_drift.py` run against the Pi by default; `REMOTE=<ref>` / `--ref <ref>` still target a cloud project. Details and the list of open security decisions are in [`supabase/README.md`](supabase/README.md).

---

##  License

This project is developed for archaeological research and conservation purposes. All rights reserved.
