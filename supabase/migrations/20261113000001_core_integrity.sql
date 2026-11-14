-- 20261113000001_core_integrity.sql
--
-- Additive integrity fixes for public.artifacts. Idempotent, one transaction, nothing is dropped
-- and no existing policy is tightened.
--
--  * artifacts had NO UPDATE policy: with RLS on, the app's updates (image_path, model_url) matched
--    0 rows and "succeeded" silently. An UPDATE policy for signed-in users fixes that.
--  * artifacts was not in the supabase_realtime publication, so the app's realtime stream never
--    received changes.
--  * updated_at (+ trigger), tripo_task_id (lets a paid Tripo generation be recovered from any
--    device), CHECKs for status and crack_percentage, indexes for the dashboard queries.
--
-- Revert (manual): drop policy "artifacts_update_authenticated" on public.artifacts;
--   alter publication supabase_realtime drop table public.artifacts;

begin;

alter table public.artifacts add column if not exists updated_at timestamptz not null default now();
alter table public.artifacts add column if not exists tripo_task_id text;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.artifacts'::regclass and conname = 'artifacts_status_check'
  ) then
    alter table public.artifacts
      add constraint artifacts_status_check
      check (status is null or status in
        ('pending', 'processing', 'complete', 'failed', 'cataloged', 'restoration'));
  end if;

  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.artifacts'::regclass and conname = 'artifacts_crack_percentage_check'
  ) then
    alter table public.artifacts
      add constraint artifacts_crack_percentage_check
      check (crack_percentage is null or (crack_percentage >= 0 and crack_percentage <= 100));
  end if;
end
$$;

create index if not exists artifacts_created_at_idx on public.artifacts (created_at desc);
create index if not exists sensor_data_created_at_idx on public.sensor_data (created_at desc);

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end
$$;

drop trigger if exists artifacts_set_updated_at on public.artifacts;
create trigger artifacts_set_updated_at
  before update on public.artifacts
  for each row execute function public.set_updated_at();

drop policy if exists "artifacts_update_authenticated" on public.artifacts;
create policy "artifacts_update_authenticated"
  on public.artifacts
  for update
  to authenticated
  using (true)
  with check (true);

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'artifacts'
  ) then
    alter publication supabase_realtime add table public.artifacts;
  end if;
end
$$;

commit;

notify pgrst, 'reload schema';
