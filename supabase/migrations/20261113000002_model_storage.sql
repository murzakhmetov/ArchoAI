-- 20261113000002_model_storage.sql
--
-- Batched storage for 3D models (GLB). Idempotent, one transaction, additive only.
--
-- IMPORTANT: buckets do NOT add capacity. The Free plan gives 1 GB for ALL buckets of the project
-- together (50 MB per file). "Batches" here are logical: buckets models-01..04 are filled in order,
-- each with a capacity, and one global cap (app_limits.storage_total_cap_bytes, counted over
-- storage.objects of EVERY bucket, images included) stops uploads before the plan quota is hit.
-- After upgrading the plan, raise the cap and add batches with plain INSERTs into model_buckets.
--
-- The buckets themselves are created by `supabase seed buckets` (supabase/config.toml), never by SQL.
--
-- Objects in storage cannot be deleted with SQL (Supabase blocks it): use the Storage API or
-- `supabase storage rm`.
--
-- Revert (manual): drop the four "models_*" policies on storage.objects, then drop the functions
-- and tables created here (model_files, model_buckets, app_limits).

begin;

-- ---------------------------------------------------------------------------
-- Limits (data, not code: change a row instead of redeploying)
-- ---------------------------------------------------------------------------
create table if not exists public.app_limits (
  key          text primary key,
  value_bigint bigint not null check (value_bigint >= 0),
  note         text,
  updated_at   timestamptz not null default now()
);
alter table public.app_limits enable row level security;

drop policy if exists "app_limits_read" on public.app_limits;
create policy "app_limits_read" on public.app_limits for select to authenticated using (true);

insert into public.app_limits (key, value_bigint, note) values
  ('storage_total_cap_bytes', 1000000000,
   'Free plan: 1 GB for ALL buckets together. Decimal bytes, about 7% safety margin. Raise after an upgrade.'),
  ('model_max_file_bytes', 52428800,
   'Free plan: 50 MB per uploaded file.')
on conflict (key) do nothing;

-- ---------------------------------------------------------------------------
-- Batch registry
-- ---------------------------------------------------------------------------
create table if not exists public.model_buckets (
  bucket_id      text primary key,
  batch_no       integer not null unique check (batch_no >= 0),
  capacity_bytes bigint  not null check (capacity_bytes >= 0),
  is_active      boolean not null default true,
  note           text,
  created_at     timestamptz not null default now()
);
alter table public.model_buckets enable row level security;

drop policy if exists "model_buckets_read" on public.model_buckets;
create policy "model_buckets_read" on public.model_buckets for select to authenticated using (true);

insert into public.model_buckets (bucket_id, batch_no, capacity_bytes, is_active, note) values
  ('models-01', 1, 225000000, true,  'batch 1, filled first'),
  ('models-02', 2, 225000000, true,  'batch 2, used when batch 1 is full'),
  ('models-03', 3, 225000000, true,  'batch 3'),
  ('models-04', 4, 225000000, true,  'batch 4'),
  ('3d1',       0,         0, false, 'legacy bucket: read only, no new files are routed here')
on conflict (bucket_id) do nothing;

-- ---------------------------------------------------------------------------
-- One row per stored model file
-- ---------------------------------------------------------------------------
create table if not exists public.model_files (
  id            uuid primary key default gen_random_uuid(),
  artifact_id   uuid references public.artifacts (id) on delete set null,
  bucket_id     text not null references public.model_buckets (bucket_id) on delete restrict,
  object_path   text not null check (object_path ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'),
  size_bytes    bigint not null check (size_bytes > 0),
  content_type  text not null default 'model/gltf-binary',
  sha256        text check (sha256 is null or sha256 ~ '^[0-9a-f]{64}$'),
  source        text not null default 'tripo' check (source in ('tripo', 'upload', 'script')),
  tripo_task_id text,
  created_at    timestamptz not null default now(),
  unique (bucket_id, object_path)
);
create index if not exists model_files_artifact_idx on public.model_files (artifact_id);
alter table public.model_files enable row level security;

drop policy if exists "model_files_read" on public.model_files;
create policy "model_files_read" on public.model_files for select to anon, authenticated using (true);
-- No INSERT/UPDATE/DELETE policy on purpose: rows are written by register_model_file() only.

alter table public.artifacts
  add column if not exists model_file_id uuid references public.model_files (id) on delete set null;

-- ---------------------------------------------------------------------------
-- Usage helpers. SECURITY DEFINER because storage.objects is not readable by anon.
-- They only return numbers, never rows of other tables.
-- ---------------------------------------------------------------------------
create or replace function public.storage_total_used_bytes()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(nullif(o.metadata ->> 'size', '')::bigint), 0)::bigint
  from storage.objects o
$$;

create or replace function public.model_bucket_used_bytes(p_bucket_id text)
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(nullif(o.metadata ->> 'size', '')::bigint), 0)::bigint
  from storage.objects o
  where o.bucket_id = p_bucket_id
$$;

-- Used by the storage INSERT policy below, so the quota also holds for clients that never call
-- pick_model_bucket(). Intentionally loose (no incoming size is known at policy time): the worst
-- overshoot is one file (<= 50 MB) over the cap, and the cap keeps ~70 MB of slack to the real quota.
create or replace function public.model_upload_allowed(p_bucket_id text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
           select 1
           from public.model_buckets b
           where b.bucket_id = p_bucket_id
             and b.is_active
             and public.model_bucket_used_bytes(b.bucket_id) < b.capacity_bytes
         )
     and public.storage_total_used_bytes() <
         (select l.value_bigint from public.app_limits l where l.key = 'storage_total_cap_bytes')
$$;

-- ---------------------------------------------------------------------------
-- RPC: which bucket should the next file go to? First active batch with room, in batch order.
-- ---------------------------------------------------------------------------
create or replace function public.pick_model_bucket(p_size_bytes bigint default 0)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_cap_total bigint;
  v_max_file  bigint;
  v_bucket    text;
begin
  if p_size_bytes is null or p_size_bytes < 0 then
    raise exception 'invalid size' using errcode = '22023';
  end if;

  select l.value_bigint into v_cap_total from public.app_limits l where l.key = 'storage_total_cap_bytes';
  select l.value_bigint into v_max_file  from public.app_limits l where l.key = 'model_max_file_bytes';

  if p_size_bytes > coalesce(v_max_file, 52428800) then
    raise exception 'model file is larger than the % byte limit', coalesce(v_max_file, 52428800)
      using errcode = '54000';
  end if;

  if public.storage_total_used_bytes() + p_size_bytes > coalesce(v_cap_total, 1000000000) then
    raise exception 'storage_full: the project storage cap is reached' using errcode = '53100';
  end if;

  select b.bucket_id
    into v_bucket
  from public.model_buckets b
  where b.is_active
    and public.model_bucket_used_bytes(b.bucket_id) + p_size_bytes <= b.capacity_bytes
  order by b.batch_no
  limit 1;

  if v_bucket is null then
    raise exception 'storage_full: every model batch is full' using errcode = '53100';
  end if;

  return v_bucket;
end
$$;

-- ---------------------------------------------------------------------------
-- RPC: record an uploaded file. Writes ONLY model_files (it must not do what RLS denies the caller,
-- so artifacts.model_url / model_file_id are set by the client through normal RLS).
-- The object must already exist in storage; its real size and type are read from storage itself.
-- ---------------------------------------------------------------------------
create or replace function public.register_model_file(
  p_bucket_id     text,
  p_object_path   text,
  p_artifact_id   uuid default null,
  p_sha256        text default null,
  p_source        text default 'tripo',
  p_tripo_task_id text default null
)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_size bigint;
  v_type text;
  v_id   uuid;
begin
  if not exists (select 1 from public.model_buckets b where b.bucket_id = p_bucket_id) then
    raise exception 'unknown model bucket %', p_bucket_id using errcode = '22023';
  end if;

  select nullif(o.metadata ->> 'size', '')::bigint, coalesce(o.metadata ->> 'mimetype', 'model/gltf-binary')
    into v_size, v_type
  from storage.objects o
  where o.bucket_id = p_bucket_id and o.name = p_object_path;

  if not found or v_size is null then
    raise exception 'object % not found in bucket %', p_object_path, p_bucket_id using errcode = 'P0002';
  end if;

  insert into public.model_files
    (artifact_id, bucket_id, object_path, size_bytes, content_type, sha256, source, tripo_task_id)
  values
    (p_artifact_id, p_bucket_id, p_object_path, v_size, v_type, lower(p_sha256), p_source, p_tripo_task_id)
  on conflict (bucket_id, object_path) do update
    set size_bytes    = excluded.size_bytes,
        content_type  = excluded.content_type,
        sha256        = coalesce(excluded.sha256, public.model_files.sha256),
        artifact_id   = coalesce(excluded.artifact_id, public.model_files.artifact_id),
        tripo_task_id = coalesce(excluded.tripo_task_id, public.model_files.tripo_task_id)
  returning id into v_id;

  return v_id;
end
$$;

-- ---------------------------------------------------------------------------
-- Usage per batch, for dashboards and for the app.
-- ---------------------------------------------------------------------------
create or replace function public.model_storage_usage()
returns table (
  bucket_id      text,
  batch_no       integer,
  is_active      boolean,
  capacity_bytes bigint,
  used_bytes     bigint,
  free_bytes     bigint,
  file_count     bigint
)
language sql
stable
security definer
set search_path = ''
as $$
  select b.bucket_id,
         b.batch_no,
         b.is_active,
         b.capacity_bytes,
         coalesce(u.used, 0)::bigint,
         greatest(b.capacity_bytes - coalesce(u.used, 0), 0)::bigint,
         coalesce(u.n, 0)::bigint
  from public.model_buckets b
  left join (
    select o.bucket_id, sum(nullif(o.metadata ->> 'size', '')::bigint) as used, count(*) as n
    from storage.objects o
    group by o.bucket_id
  ) u on u.bucket_id = b.bucket_id
  order by b.batch_no
$$;

-- Grants: nothing is callable by PUBLIC; each role gets exactly what it needs.
revoke all on function public.storage_total_used_bytes()                      from public;
revoke all on function public.model_bucket_used_bytes(text)                   from public;
revoke all on function public.model_upload_allowed(text)                      from public;
revoke all on function public.pick_model_bucket(bigint)                       from public;
revoke all on function public.register_model_file(text, text, uuid, text, text, text) from public;
revoke all on function public.model_storage_usage()                           from public;

grant execute on function public.storage_total_used_bytes()                   to anon, authenticated;
grant execute on function public.model_bucket_used_bytes(text)                to anon, authenticated;
grant execute on function public.model_upload_allowed(text)                   to anon, authenticated;
grant execute on function public.pick_model_bucket(bigint)                    to anon, authenticated;
grant execute on function public.register_model_file(text, text, uuid, text, text, text) to anon, authenticated;
grant execute on function public.model_storage_usage()                        to authenticated;
-- Supabase's default privileges also grant EXECUTE to anon: usage numbers are for signed-in users only.
revoke execute on function public.model_storage_usage() from anon;

-- ---------------------------------------------------------------------------
-- Storage policies for the model buckets (artifact-images and 3d1 keep their own policies).
-- ---------------------------------------------------------------------------
drop policy if exists "models_insert_guarded" on storage.objects;
create policy "models_insert_guarded"
  on storage.objects
  for insert
  to anon, authenticated
  with check (
    public.model_upload_allowed(bucket_id)
    and name ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'
  );

drop policy if exists "models_select_authenticated" on storage.objects;
create policy "models_select_authenticated"
  on storage.objects
  for select
  to authenticated
  using (bucket_id in (select b.bucket_id from public.model_buckets b));

drop policy if exists "models_update_authenticated" on storage.objects;
create policy "models_update_authenticated"
  on storage.objects
  for update
  to authenticated
  using (bucket_id in (select b.bucket_id from public.model_buckets b))
  with check (bucket_id in (select b.bucket_id from public.model_buckets b)
              and name ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$');

drop policy if exists "models_delete_authenticated" on storage.objects;
create policy "models_delete_authenticated"
  on storage.objects
  for delete
  to authenticated
  using (bucket_id in (select b.bucket_id from public.model_buckets b));

commit;

notify pgrst, 'reload schema';
