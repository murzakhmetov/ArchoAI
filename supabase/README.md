# Supabase backend

Runs **on the Raspberry Pi** as a self-hosted Supabase stack (see [`server/README.md`](../server/README.md)): the database and the files live on the Pi. The old cloud project `wvhdhfddtusppjmsgmvn` (Free plan) is no longer used; its data was copied to the Pi on 2026-11-26 (`server/import_from_cloud.py`). Everything here is plain SQL plus one `config.toml`, applied with the Supabase CLI.

## What is in this folder

| File | Purpose |
| --- | --- |
| `migrations/20260101000000_baseline_tables.sql` | The two original tables (`artifacts`, `sensor_data`) and the policies that existed before the other files, rebuilt from the live catalog. For a **fresh** project; on the live project it changes nothing. |
| `migrations/20261113000001_core_integrity.sql` | `artifacts`: UPDATE policy (the app's updates used to match 0 rows), realtime on `artifacts`, `updated_at`, `tripo_task_id`, CHECKs for `status` and `crack_percentage`, indexes. |
| `migrations/20261113000002_model_storage.sql` | Batched 3D model storage: `app_limits`, `model_buckets`, `model_files`, RPCs `pick_model_bucket()`, `register_model_file()`, `model_storage_usage()`, storage policies for `models-01..04`. |
| `migrations/20261113000003_sensor_data_device_insert.sql` | Lets the ESP32 insert readings again (value ranges, 1 insert per 2 s for `anon`). **Security trade-off**, kept in its own file; the revert statements are in its header. Narrowed to `anon` by `000007`. |
| `migrations/20261113000004_private_helpers.sql` | Moves internal helper functions to the `private` schema so they are not REST endpoints. |
| `migrations/20261113000005_harden.sql` | Total-cap guard on **every** bucket (restrictive policies), `anon` may insert only the three sensor columns, `register_model_file()` cannot attach or re-point a file, `link_model_files()`, path checks on uploads. |
| `migrations/20261113000006_revoke_unused_table_privileges.sql` | Removes TRUNCATE / REFERENCES / TRIGGER from `anon` and `authenticated` (TRUNCATE ignores row level security). |
| `migrations/20261113000007_sensor_data_authenticated_readonly.sql` | Signed-in users can no longer insert into `sensor_data` (a future-dated row locked the ESP32 out), and the device rate limit ignores future-dated rows. |
| `migrations/20261113000008_replace_in_full_batch.sql` | Replacing a model that lives in a full batch is allowed (an upsert of an existing name), so re-generating a model no longer leaves a second copy in the next batch. A new file is still refused in a full batch. |
| `migrations/20261126000001_pi_local_server.sql` | On the Pi: the storage cap becomes 20 GB and each model batch 5 GB (the 1 GB Free-plan quota does not apply). Only the shipped cloud values are changed. Not for a Free-plan project. |
| `migrations/20261128000003_soil_and_air_ppm.sql` | New nullable readings in `sensor_data`: `soil_moisture` (%), `soil_temperature` (°C) and `air_ppm` (air quality in ppm, CO2 equivalent). `anon` may insert them within their ranges; null is allowed (older firmware, a sensor that is not connected). |
| `config.toml` | The stack on the Pi: API gateway on port 54331 of 127.0.0.1 (the network reaches it on 54321 through `server/systemd/archoai-api-lan.service`), its own secrets from `supabase/.env` (`env(...)`), S3 protocol off. Declares the four model buckets `models-01..04` (public, 50 MiB, glTF binary types; the `.glb` name is enforced by the storage policy, not by the bucket) and the two older buckets with limits: `artifact-images` (10 MiB, images) and `3d1` (50 MiB, legacy). |
| `snapshots/` | The state before the changes (policies, columns, buckets, grants) and the Supabase advisor reports before and after. |
| `tests/smoke.sh` | Policy and RPC checks that run inside rolled-back transactions. |
| `tests/local_sql.py` | Runs a multi-statement check on the Pi's database (the CLI cannot send those locally) and prints it like `supabase db query`. |
| `tests/migration_drift.py` | Checks that the migration files still describe the live database (see "Tests"). |

## Apply

**On the Pi** nothing is applied by hand: `bash server/setup.sh` (or `sudo systemctl restart archoai-supabase`) starts the
stack; on a new database it applies every file of `migrations/` in name order and creates the buckets from `config.toml`.
A new migration file is applied to the running database with `supabase migration up` (the local migration history
lists every file: `supabase migration list`). Then run the checks:

```bash
bash supabase/tests/smoke.sh                 # every line must say PASS
python3 supabase/tests/migration_drift.py    # PASS: the files describe the database
```

**On a Supabase cloud project** (the old setup) apply the files **in name order**, one by one. Each file is idempotent and
runs in one transaction. Never re-apply one file from the middle on its own: an older file can put back older code. For
example `000002` re-creates its helper functions in the exposed `public` schema, and `000004` replaces the newer versions
that `000005` installed. To re-apply, run every file from the one you want through the last one, in order. Skip
`20261126000001_pi_local_server.sql` on a Free-plan project.

```bash
supabase db query --remote=<ref> -f supabase/migrations/<file>.sql
supabase seed buckets --linked --project-ref <ref>          # creates the buckets from config.toml
REMOTE=<ref> bash supabase/tests/smoke.sh
```

* Buckets are created with `seed buckets` (or the dashboard), never with SQL: Supabase protects `storage.buckets`.
* Storage objects cannot be deleted with SQL either; use the Storage API or `supabase storage rm`.

## How the batches work

> On the Pi, migration `20261126000001_pi_local_server.sql` sets the cap to **20 GB** and each batch to **5 GB**; the 1 GB quota and the 900 MB / 225 MB figures below are the values of the old Free-plan cloud project. The mechanism is the same.

Buckets do **not** add capacity. The Free plan gives 1 GB for **all** buckets of the project together, and 50 MB per file. The "batches" are a logical split:

* `model_buckets` lists the batches in order with a capacity each (4 x 225 MB) and an `is_active` flag. The legacy bucket `3d1` is batch 0 and inactive: it is read but never chosen.
* `pick_model_bucket(size)` returns the first active batch that still has room. When `models-01` is full, new files go to `models-02`, and so on. Errors: `53100` (`storage_full`), `54000` (file larger than 50 MB), `22023` (bad size).
* One global cap, `app_limits.storage_total_cap_bytes` (900,000,000 bytes, 10% below the real quota), is counted over **every** bucket, images and the legacy bucket included. When it is reached, uploads stop instead of hitting the plan quota.
* The cap is enforced in the database for every client, not only for ones that call the RPC: two restrictive policies on `storage.objects` (`storage_total_cap_guard_insert` and `_update`) are AND-ed with every other policy for `anon` and `authenticated`. No file size is known at policy time, and an upload that has not finished is not in `storage.objects` yet, so a few uploads in flight together can each go over the cap; the 100 MB of slack is there for that.
* `register_model_file()` records a stored file in `model_files` (bucket, path, real size read from storage, SHA-256, source, Tripo task id). `anon` can register a file but cannot attach it to an artifact, and a recorded file is never re-pointed to another artifact. `link_model_files()` (signed-in users only) links recorded files to the artifacts whose `model_url` points at them; the app calls it once per session after sign-in.
* Bandwidth (egress) is a separate Free plan limit that nothing here controls. The model buckets are public, so every viewer of a model uses it.

The four batches add up to the cap (4 x 225 MB = 900 MB), so models can use all of it; the cap, which also counts images and the legacy bucket, always wins. Do not raise the cap above about 900,000,000 bytes on the Free plan: the 100 MB below the 1 GB quota has to absorb uploads that are still in flight, and the cap only counts what `storage.objects` reports. To get real extra capacity you need a plan upgrade (then raise the cap and the batch capacities together) or a second Supabase project.

Add a batch:

1. Add a `[storage.buckets.models-05]` block to `config.toml` and run `supabase seed buckets --linked`.
2. `insert into public.model_buckets (bucket_id, batch_no, capacity_bytes) values ('models-05', 5, 225000000);`

Batch capacities are only the split between buckets; the cap always wins. If they add up to more than the cap, the last batch simply never fills.

## Who may do what

| Role | `artifacts` | `sensor_data` | model buckets | RPCs |
| --- | --- | --- | --- | --- |
| `anon` (publishable key, public in the repo) | insert | read; insert `temperature`, `humidity`, `air_quality`, `air_ppm`, `soil_moisture`, `soil_temperature` within ranges, 1 per 2 s | upload `.glb` while there is room (no upsert) | `pick_model_bucket`, `register_model_file` |
| `authenticated` (app users) | read, insert, update | read | read, upload, replace (upsert, also in a full batch), delete (not in the legacy `3d1`) | the same plus `model_storage_usage`, `link_model_files` |

Ranges for `sensor_data`: temperature -40..125, humidity 0..100, `air_quality` 0..4095 (the raw 12-bit ADC value); optional (null allowed): `air_ppm` 0..10000, `soil_moisture` 0..100, `soil_temperature` -55..125. `anon` cannot set `id` or `created_at`, and the rate limit ignores rows dated in the future, so a bad timestamp cannot lock the device out.

## Open decisions

These were found like this and are left as they are. Each one is a product choice, not a bug.

* **Anyone can sign up, and a signed-up user can edit everything.** The project's auth settings (`/auth/v1/settings`, checked on 2026-11-14) have `disable_signup: false` and `mailer_autoconfirm: true`, so a new account works at once, with no e-mail confirmation. The app has a register screen, so this is intended, but the `authenticated` policies are `using (true)`: any account can read, insert and update every artifact, and upload, replace and delete files in `models-01..04` and `artifact-images`. Either turn sign-up off in the dashboard (Authentication, "Allow new users to sign up") if the set of users is fixed, or add an owner column to `artifacts` and `model_files` with per-user policies.
* **`anon` can insert artifact rows and upload to `artifact-images` and `3d1`** ("Allow public insert", "Allow public upload ..."). The Python rig uses the publishable key. The total cap, the size limits in `config.toml` and the policy guards bound the damage, but anyone with the key can fill the quota with images. Require sign-in for the rig, or move it to a secret key kept out of the repository, to close this.
* **The ESP32 inserts with the publishable key**, which is public (it is in `monitoring.cpp` and inside the app). On the Pi the gateway the CLI generates even lets any other `sb_...` value through as an anonymous request, so the publishable key is no access control at all: the anonymous role's policies are. Range checks, the column-level grant and the rate limit make abuse cheap to ignore but not impossible. A per-device token behind an Edge Function would close it.
* **Cap versus quota.** The cap counts `storage.objects` metadata sizes. If Supabase counts something else against the 1 GB, lower `storage_total_cap_bytes`.
* **Metadata from `anon` is not authenticated.** The Python rig registers its files as `anon`, so `sha256`, `source` and `tripo_task_id` in `model_files` are whatever the caller sent, and the first writer wins. They are bookkeeping, nothing reads them for access decisions. `anon` can also estimate how full the current batch is by calling `pick_model_bucket(size)` with different sizes.

## Tests

* `tests/smoke.sh` checks the policies and RPCs as `anon` and `authenticated` (33 checks), on the Pi by default (`REMOTE=<ref>` for a cloud project, one CLI call per check, several minutes). Do not run other `supabase db query` commands at the same time: the CLI logs in with a temporary role per call and two calls can collide (the script retries a few times). It prints `PASS` or `FAIL` per check and exits non-zero on any failure. It inserts straight into `storage.objects`, so it tests the policies, not the Storage API: resumable (TUS) and S3 uploads are not covered.
* `tests/migration_drift.py` re-applies all migration files in one rolled-back transaction and compares fingerprints of the schema before and after (298 facts: functions with bodies, grants and settings, columns, table and column grants, RLS flags, policies, constraints, indexes, triggers, realtime membership, seed rows). It also lists objects whose names appear in no file. A clean run means the files describe the live project; a hand edit in the dashboard shows up as a difference. It does not see hand-added columns or auto-named indexes.
* Verified once on 2026-11-14 in a rolled-back transaction: after dropping every object the migrations create (tables, functions, policies on `storage.objects`, the `private` schema), the nine files rebuilt the schema with all 298 facts identical to the live project (seed rows included). That script is not in the repository on purpose: it drops objects and is only safe because the transaction is rolled back.
* The baseline was also compared with the pre-change snapshot (`snapshots/2026-11-13_before.json`): same columns, same 14 policies, same grants, identity and realtime.

## Known leftovers

* The Supabase advisor reports 8 warnings (`snapshots/2026-11-13_advisors_after.txt`): "leaked password protection" (a dashboard setting), two permissive INSERT policies on `artifacts` (the older ones above), and six notices that the intended RPCs are `SECURITY DEFINER` and callable by `anon` or signed-in users (`pick_model_bucket`, `register_model_file`, `model_storage_usage`, `link_model_files`). All of them run with `search_path = ''`; `register_model_file` and `link_model_files` also check the caller's role inside, and the others are limited by their grants.
* The Pi's publishable key and the Wi-Fi credentials are in `monitoring.cpp`. Change the Wi-Fi password if the repository is public.
* `artifact-images` still carries four redundant "Give users authenticated access to folder ..." policies that the dashboard once generated. They are harmless and are recreated by the baseline so that a fresh project matches.
* `scripts/scripts/generate_smooth_data.py` seeds past readings with `created_at` and 144 rows at once. The publishable key cannot do that any more, so it needs a secret key: on the Pi it reads `ARCHOAI_SECRET_KEY` from `supabase/.env`, otherwise `SUPABASE_SERVICE_KEY` from the shell environment (never put a secret key in `archoai_app/.env`: that file is bundled into the app).
* A regenerated model replaces its previous file in the same batch. If that batch refuses (the cap is reached, or the batch was retired), the new file goes to another batch and the old object stays until someone removes it.
