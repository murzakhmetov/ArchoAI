-- 20261113000008_replace_in_full_batch.sql
--
-- Replacing a model that lives in a FULL batch used to be refused. The Storage API's upsert is
-- INSERT ... ON CONFLICT DO UPDATE, and the INSERT policy of the batches only let a file in while the batch
-- still had room. So re-generating such a model wrote a second copy into the next batch and left the old one
-- behind, wasting quota exactly when it is short.
--
-- The INSERT policy now also lets an upsert through when the object already exists in an active batch: that
-- is a replacement, not a new file. Everything else is unchanged:
--  * a NEW file is still refused in a full batch;
--  * the total cap (the restrictive policies) still applies to the replacement;
--  * anon gains nothing: an upsert also needs SELECT and UPDATE policies, which only signed-in users have,
--    and a plain insert of a name that exists is a duplicate-key error.
--
-- Idempotent, one transaction. Revert (manual): recreate "models_insert_guarded" as in 000005 and
--   drop function private.model_replace_allowed(text, text);

begin;

create or replace function private.model_replace_allowed(p_bucket_id text, p_name text)
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
         )
     and exists (
           select 1
           from storage.objects o
           where o.bucket_id = p_bucket_id
             and o.name = p_name collate "C"
         )
$$;

revoke all on function private.model_replace_allowed(text, text) from public;
grant execute on function private.model_replace_allowed(text, text) to anon, authenticated;

drop policy if exists "models_insert_guarded" on storage.objects;
create policy "models_insert_guarded"
  on storage.objects
  for insert
  to anon, authenticated
  with check (
    (private.model_upload_allowed(bucket_id) or private.model_replace_allowed(bucket_id, name))
    and name ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,198}[.]glb$'
    and name !~ '(^|/)[.]{1,2}(/|$)'
  );

commit;

notify pgrst, 'reload schema';
