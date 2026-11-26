-- Self-hosted on the Raspberry Pi: the 1 GB Free-plan quota no longer applies.
--
-- The database and the files of the project live on the Pi (see server/README.md). The storage cap
-- and the batch capacities set by 20261113000002 / 000005 were sized for the Supabase Free plan
-- (1 GB for all buckets of a project together). On the Pi the limit is the SD card, so the cap
-- becomes 20 GB, split over the four model batches (4 x 5 GB). The mechanism itself is unchanged:
-- pick_model_bucket() still fills the batches in order and the storage policies still stop every
-- upload once the cap is reached.
--
-- Only the values this project shipped with are changed, so a value set by hand is kept. Lower the
-- cap when the card gets full (df -h /):
--   update public.app_limits set value_bigint = <bytes> where key = 'storage_total_cap_bytes';
--
-- Do NOT apply this file to a Supabase Free-plan project: the cap would then no longer stop uploads
-- before the plan quota. Revert:
--   update public.app_limits set value_bigint = 900000000 where key = 'storage_total_cap_bytes';
--   update public.model_buckets set capacity_bytes = 225000000 where batch_no between 1 and 4;

begin;

update public.app_limits
   set value_bigint = 20000000000,
       note = 'Raspberry Pi server: files live on the SD card. 20 GB for all buckets together; lower it when the card fills up.',
       updated_at = now()
 where key = 'storage_total_cap_bytes'
   and value_bigint in (900000000, 1000000000);

update public.model_buckets
   set capacity_bytes = 5000000000
 where bucket_id in ('models-01', 'models-02', 'models-03', 'models-04')
   and capacity_bytes = 225000000;

commit;
