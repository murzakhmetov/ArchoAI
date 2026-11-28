-- High-quality 3D models: a model file may be up to 200 MiB on the Raspberry Pi.
--
-- Tripo's best quality (model v3.1, geometry_quality "detailed" = Ultra, detailed PBR textures) gives
-- GLB files above the 50 MiB per-file limit of the Supabase Free plan (a standard-quality turntable
-- scan was already 44 MB). On the Pi that limit only exists in this project's own settings, so it
-- becomes 200 MiB here and in supabase/config.toml (bucket file_size_limit). pick_model_bucket()
-- reads this row, so larger files are no longer refused with 54000.
--
-- Only the shipped value is changed. Do NOT apply to a Free-plan project: its platform limit stays
-- 50 MiB whatever this row says. Revert:
--   update public.app_limits set value_bigint = 52428800 where key = 'model_max_file_bytes';

begin;

update public.app_limits
   set value_bigint = 209715200,
       note = 'Raspberry Pi server: 200 MiB per model file (Tripo Ultra quality). Must match file_size_limit of the model buckets in supabase/config.toml.',
       updated_at = now()
 where key = 'model_max_file_bytes'
   and value_bigint = 52428800;

commit;
