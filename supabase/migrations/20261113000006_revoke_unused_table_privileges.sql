-- 20261113000006_revoke_unused_table_privileges.sql
--
-- anon and authenticated never need TRUNCATE, REFERENCES or TRIGGER on the app tables, but Supabase's
-- default privileges hand them out. TRUNCATE is the dangerous one: it ignores row level security, so the
-- privilege itself is the only thing between a role and an empty table. PostgREST has no TRUNCATE verb, and
-- this keeps it that way if a function or an extension ever exposes one.
-- Nothing in the app, the scripts or the ESP32 firmware uses these privileges. SELECT / INSERT / UPDATE /
-- DELETE stay as they are (row level security decides those).
--
-- Idempotent: revoking a privilege that is not there is a no-op.
-- Revert (manual): grant truncate, references, trigger on table <the tables below> to anon, authenticated;

begin;

revoke truncate, references, trigger on table
  public.artifacts,
  public.sensor_data,
  public.app_limits,
  public.model_buckets,
  public.model_files
from anon, authenticated;

commit;

notify pgrst, 'reload schema';
