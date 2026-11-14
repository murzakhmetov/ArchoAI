-- 20261113000004_private_helpers.sql
--
-- Moves the three internal helper functions out of the REST-exposed `public` schema into `private`
-- (not exposed by PostgREST), so they are no longer callable as /rest/v1/rpc/* endpoints.
-- Public RPCs that stay on purpose:
--   pick_model_bucket()     anon + authenticated   (the Python rig uses the publishable key)
--   register_model_file()   anon + authenticated
--   model_storage_usage()   authenticated only
-- SECURITY DEFINER functions call the helpers as their owner, so callers need no grant on them.
-- Only model_upload_allowed() is evaluated by the storage INSERT policy as the calling role, so it
-- keeps EXECUTE for anon/authenticated (and USAGE on the schema).
--
-- Idempotent, one transaction.

begin;

create schema if not exists private;
revoke all on schema private from public;
grant usage on schema private to anon, authenticated;

create or replace function private.storage_total_used_bytes()
returns bigint
language sql
stable
security definer
set search_path = ''
as $$
  select coalesce(sum(nullif(o.metadata ->> 'size', '')::bigint), 0)::bigint
  from storage.objects o
$$;

create or replace function private.model_bucket_used_bytes(p_bucket_id text)
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
             and private.model_bucket_used_bytes(b.bucket_id) < b.capacity_bytes
         )
     and private.storage_total_used_bytes() <
         (select l.value_bigint from public.app_limits l where l.key = 'storage_total_cap_bytes')
$$;

revoke all on function private.storage_total_used_bytes()    from public, anon, authenticated;
revoke all on function private.model_bucket_used_bytes(text) from public, anon, authenticated;
revoke all on function private.model_upload_allowed(text)    from public;
grant execute on function private.model_upload_allowed(text) to anon, authenticated;

-- pick_model_bucket now uses the private helpers (same behaviour as before).
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

  if private.storage_total_used_bytes() + p_size_bytes > coalesce(v_cap_total, 1000000000) then
    raise exception 'storage_full: the project storage cap is reached' using errcode = '53100';
  end if;

  select b.bucket_id
    into v_bucket
  from public.model_buckets b
  where b.is_active
    and private.model_bucket_used_bytes(b.bucket_id) + p_size_bytes <= b.capacity_bytes
  order by b.batch_no
  limit 1;

  if v_bucket is null then
    raise exception 'storage_full: every model batch is full' using errcode = '53100';
  end if;

  return v_bucket;
end
$$;

revoke all on function public.pick_model_bucket(bigint) from public;
grant execute on function public.pick_model_bucket(bigint) to anon, authenticated;

-- The storage INSERT policy now calls the private helper.
drop policy if exists "models_insert_guarded" on storage.objects;
create policy "models_insert_guarded"
  on storage.objects
  for insert
  to anon, authenticated
  with check (
    private.model_upload_allowed(bucket_id)
    and name ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'
  );

-- The old public copies are not used any more.
drop function if exists public.model_upload_allowed(text);
drop function if exists public.model_bucket_used_bytes(text);
drop function if exists public.storage_total_used_bytes();

commit;

notify pgrst, 'reload schema';
