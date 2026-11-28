-- Turntable scans: every artifact can keep the photos of all scan angles.
--
-- scripts/scripts/turntable_scan.py turns the artifact on the turntable (Arduino + NEMA 17) and takes
-- one photo every 60 degrees. image_path keeps the main photo (marked by DeepCrack); photo_urls
-- holds the public links of all angle photos in turning order (0, 60, ..., 300 degrees). Older rows
-- and the other scanners leave it empty. Readable and writable like every other artifacts column.
--
-- Revert: alter table public.artifacts drop column if exists photo_urls;

begin;

alter table public.artifacts
  add column if not exists photo_urls text[] not null default '{}';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.artifacts'::regclass and conname = 'artifacts_photo_urls_count_check'
  ) then
    alter table public.artifacts
      add constraint artifacts_photo_urls_count_check check (cardinality(photo_urls) <= 36);
  end if;
end
$$;

commit;
