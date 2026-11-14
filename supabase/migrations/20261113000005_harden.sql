-- 20261113000005_harden.sql
--
-- Hardening after an independent audit of migrations 1-4. Idempotent, one transaction.
--
--  * sensor_data: anon may now insert ONLY (temperature, humidity, air_quality). It can no longer set
--    created_at or id, so one request cannot post a future-dated row and lock the device out of the
--    rate limiter, and a bulk backfill with fake timestamps is impossible. (A dev script that needs
--    backdated rows must use the service key.)
--  * ONE storage cap for EVERY bucket: restrictive INSERT/UPDATE policies on storage.objects stop all
--    uploads once total usage reaches the cap, including the older artifact-images and 3d1 buckets
--    that used to be uncapped. The cap is lowered to 900,000,000 bytes so that a few parallel uploads
--    (an in-flight upload is not in storage.objects yet) still stay under the 1 GB plan quota.
--  * models_select/update/delete no longer cover the legacy bucket 3d1 (batch_no 0).
--  * register_model_file: an existing row is never re-pointed to another artifact, and anonymous
--    callers cannot attach a file to an artifact. model_files is readable by signed-in users only.
--  * One definition of the cap (private.storage_cap_bytes, default when the row is missing) used by
--    every check; object sizes are parsed safely; object names may not contain "." or ".." segments.
--  * link_model_files(): staff can link script-created files to their artifacts by URL.
--
-- Revert pieces manually (see the statements below); nothing here changes existing data.

begin;

-- ---------------------------------------------------------------------------
-- 1. sensor_data: anon writes only the three measurement columns
-- ---------------------------------------------------------------------------
revoke insert on public.sensor_data from anon;
grant insert (temperature, humidity, air_quality) on public.sensor_data to anon;

-- ---------------------------------------------------------------------------
-- 2. Safe size parsing and ONE cap definition
-- ---------------------------------------------------------------------------
create or replace function private.object_size(p_metadata jsonb)
returns bigint
language sql
immutable
set search_path = ''
as $$
  select case when p_metadata ->> 'size' ~ '^[0-9]{1,15}$' then (p_metadata ->> 'size')::bigint else 0 end
$$;

create or replace function private.storage_cap_bytes()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(
    (select l.value_bigint from public.app_limits l where l.key = 'storage_total_cap_bytes'),
    900000000
  )
$$;

-- Lowering the cap is the safer direction; only the value this project shipped with is changed.
update public.app_limits
   set value_bigint = 900000000,
       note = 'Free plan: 1 GB for ALL buckets together. Kept well below it because uploads in flight are not counted yet. Raise after an upgrade.',
       updated_at = now()
 where key = 'storage_total_cap_bytes' and value_bigint = 1000000000;

create or replace function private.storage_total_used_bytes()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(private.object_size(o.metadata)), 0)::bigint from storage.objects o
$$;

create or replace function private.model_bucket_used_bytes(p_bucket_id text)
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(private.object_size(o.metadata)), 0)::bigint
  from storage.objects o
  where o.bucket_id = p_bucket_id
$$;

create or replace function private.storage_under_cap()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select private.storage_total_used_bytes() < private.storage_cap_bytes()
$$;

create or replace function private.model_upload_allowed(p_bucket_id text)
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
             and b.batch_no > 0
             and private.model_bucket_used_bytes(b.bucket_id) < b.capacity_bytes
         )
     and private.storage_under_cap()
$$;

revoke all on function private.object_size(jsonb)              from public;
revoke all on function private.storage_cap_bytes()             from public, anon, authenticated;
revoke all on function private.storage_total_used_bytes()      from public, anon, authenticated;
revoke all on function private.model_bucket_used_bytes(text)   from public, anon, authenticated;
revoke all on function private.storage_under_cap()             from public;
revoke all on function private.model_upload_allowed(text)      from public;
grant execute on function private.storage_under_cap()          to anon, authenticated;
grant execute on function private.model_upload_allowed(text)   to anon, authenticated;

-- ---------------------------------------------------------------------------
-- 3. RPCs on the same definitions
-- ---------------------------------------------------------------------------
create or replace function public.pick_model_bucket(p_size_bytes bigint default 0)
returns text
language plpgsql
stable
security definer
set search_path = ''
as $$
declare
  v_max_file bigint;
  v_bucket   text;
begin
  if p_size_bytes is null or p_size_bytes < 0 then
    raise exception 'invalid size' using errcode = '22023';
  end if;

  select l.value_bigint into v_max_file from public.app_limits l where l.key = 'model_max_file_bytes';

  if p_size_bytes > coalesce(v_max_file, 52428800) then
    raise exception 'model file is larger than the % byte limit', coalesce(v_max_file, 52428800)
      using errcode = '54000';
  end if;

  if private.storage_total_used_bytes() + p_size_bytes > private.storage_cap_bytes() then
    raise exception 'storage_full: the project storage cap is reached' using errcode = '53100';
  end if;

  select b.bucket_id
    into v_bucket
  from public.model_buckets b
  where b.is_active
    and b.batch_no > 0
    and private.model_bucket_used_bytes(b.bucket_id) + p_size_bytes <= b.capacity_bytes
  order by b.batch_no
  limit 1;

  if v_bucket is null then
    raise exception 'storage_full: every model batch is full' using errcode = '53100';
  end if;

  return v_bucket;
end
$$;

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
    select o.bucket_id, sum(private.object_size(o.metadata)) as used, count(*) as n
    from storage.objects o
    group by o.bucket_id
  ) u on u.bucket_id = b.bucket_id
  order by b.batch_no
$$;

-- register_model_file: record a stored file. Never re-points an existing row; an anonymous caller
-- (the rig scripts, which hold the public key) can record a file but cannot attach it to an artifact.
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
  v_size     bigint;
  v_type     text;
  v_id       uuid;
  v_artifact uuid := p_artifact_id;
begin
  if not exists (select 1 from public.model_buckets b where b.bucket_id = p_bucket_id) then
    raise exception 'unknown model bucket %', p_bucket_id using errcode = '22023';
  end if;

  if coalesce(current_setting('role', true), '') = 'anon' then
    v_artifact := null;
  end if;

  select private.object_size(o.metadata), coalesce(o.metadata ->> 'mimetype', 'model/gltf-binary')
    into v_size, v_type
  from storage.objects o
  where o.bucket_id = p_bucket_id and o.name = p_object_path;

  if not found or coalesce(v_size, 0) <= 0 then
    raise exception 'object % not found in bucket %', p_object_path, p_bucket_id using errcode = 'P0002';
  end if;

  insert into public.model_files
    (artifact_id, bucket_id, object_path, size_bytes, content_type, sha256, source, tripo_task_id)
  values
    (v_artifact, p_bucket_id, p_object_path, v_size, v_type, lower(p_sha256), p_source, p_tripo_task_id)
  on conflict (bucket_id, object_path) do update
    set size_bytes    = excluded.size_bytes,
        content_type  = excluded.content_type,
        sha256        = coalesce(excluded.sha256, public.model_files.sha256),
        artifact_id   = coalesce(public.model_files.artifact_id, excluded.artifact_id),
        tripo_task_id = coalesce(public.model_files.tripo_task_id, excluded.tripo_task_id)
  returning id into v_id;

  return v_id;
end
$$;

-- Links files recorded by the rig scripts (no artifact id) to the artifact whose model_url points at
-- them. Signed-in users only; returns how many files were linked.
create or replace function public.link_model_files()
returns integer
language plpgsql
security definer
set search_path = ''
as $$
declare
  v_linked integer;
begin
  if coalesce(current_setting('role', true), '') <> 'authenticated' then
    raise exception 'sign in first' using errcode = '42501';
  end if;

  with matched as (
    select mf.id as file_id, a.id as artifact_id
    from public.model_files mf
    join public.artifacts a
      on right(a.model_url, length('/storage/v1/object/public/' || mf.bucket_id || '/' || mf.object_path))
         = '/storage/v1/object/public/' || mf.bucket_id || '/' || mf.object_path
    where mf.artifact_id is null
  ),
  linked_files as (
    update public.model_files mf
       set artifact_id = m.artifact_id
      from matched m
     where mf.id = m.file_id
     returning mf.id
  ),
  linked_artifacts as (
    update public.artifacts a
       set model_file_id = m.file_id
      from matched m
     where a.id = m.artifact_id and a.model_file_id is null
     returning a.id
  )
  select count(*) into v_linked from linked_files;

  return v_linked;
end
$$;

revoke all on function public.pick_model_bucket(bigint)                                  from public;
revoke all on function public.model_storage_usage()                                      from public, anon;
revoke all on function public.register_model_file(text, text, uuid, text, text, text)    from public;
revoke all on function public.link_model_files()                                         from public, anon;
grant execute on function public.pick_model_bucket(bigint)                               to anon, authenticated;
grant execute on function public.model_storage_usage()                                   to authenticated;
grant execute on function public.register_model_file(text, text, uuid, text, text, text) to anon, authenticated;
grant execute on function public.link_model_files()                                      to authenticated;

-- ---------------------------------------------------------------------------
-- 4. Tables
-- ---------------------------------------------------------------------------
drop policy if exists "model_files_read" on public.model_files;
create policy "model_files_read" on public.model_files for select to authenticated using (true);

alter table public.model_files drop constraint if exists model_files_object_path_check;
alter table public.model_files
  add constraint model_files_object_path_check
  check (object_path ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'
         and object_path !~ '(^|/)[.]{1,2}(/|$)');

create index if not exists artifacts_model_file_idx on public.artifacts (model_file_id);

-- ---------------------------------------------------------------------------
-- 5. Storage policies
-- ---------------------------------------------------------------------------
-- One cap for every bucket (restrictive policies are ANDed with the permissive ones).
drop policy if exists "storage_total_cap_guard_insert" on storage.objects;
create policy "storage_total_cap_guard_insert"
  on storage.objects
  as restrictive
  for insert
  to anon, authenticated
  with check (private.storage_under_cap());

drop policy if exists "storage_total_cap_guard_update" on storage.objects;
create policy "storage_total_cap_guard_update"
  on storage.objects
  as restrictive
  for update
  to anon, authenticated
  using (true)
  with check (private.storage_under_cap());

-- Model batches only (batch_no > 0): the legacy bucket 3d1 keeps exactly the policies it had before.
drop policy if exists "models_insert_guarded" on storage.objects;
create policy "models_insert_guarded"
  on storage.objects
  for insert
  to anon, authenticated
  with check (
    private.model_upload_allowed(bucket_id)
    and name ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'
    and name !~ '(^|/)[.]{1,2}(/|$)'
  );

drop policy if exists "models_select_authenticated" on storage.objects;
create policy "models_select_authenticated"
  on storage.objects
  for select
  to authenticated
  using (bucket_id in (select b.bucket_id from public.model_buckets b where b.batch_no > 0));

drop policy if exists "models_update_authenticated" on storage.objects;
create policy "models_update_authenticated"
  on storage.objects
  for update
  to authenticated
  using (bucket_id in (select b.bucket_id from public.model_buckets b where b.batch_no > 0))
  with check (
    bucket_id in (select b.bucket_id from public.model_buckets b where b.batch_no > 0)
    and name ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'
    and name !~ '(^|/)[.]{1,2}(/|$)'
  );

drop policy if exists "models_delete_authenticated" on storage.objects;
create policy "models_delete_authenticated"
  on storage.objects
  for delete
  to authenticated
  using (bucket_id in (select b.bucket_id from public.model_buckets b where b.batch_no > 0));

commit;

notify pgrst, 'reload schema';
