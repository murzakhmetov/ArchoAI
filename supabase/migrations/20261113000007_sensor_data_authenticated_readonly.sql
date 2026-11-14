-- 20261113000007_sensor_data_authenticated_readonly.sql
--
-- Found by the final verification: any signed-in user could insert a sensor_data row with a future
-- created_at (or a chosen id). Two effects:
--  * the device rate limit (000003) counts rows newer than now() - 2 s, so one future-dated row
--    locked the ESP32 out for good;
--  * the app's "latest reading" (order by created_at desc limit 1) would show that row forever.
-- Sign-up is open on this project (disable_signup = false, mailer_autoconfirm = true), so "signed in"
-- means "anyone".
--
-- Fix:
--  * authenticated users never write sensor data: the app only reads it and the device inserts as
--    anon. The INSERT privilege and the insert policy for authenticated are removed.
--  * the rate limit ignores rows dated in the future, so a bad timestamp written by any other role
--    (a script with the service key, the SQL editor) cannot lock the device out either.
--
-- Idempotent, one transaction. Revert (manual): before this file authenticated had the table-level INSERT
-- privilege, so
--   grant insert on table public.sensor_data to authenticated;
--   and recreate the policy "sensor_data_insert_device" with `to anon, authenticated`.

begin;

revoke insert on table public.sensor_data from authenticated;

drop policy if exists "sensor_data_insert_device" on public.sensor_data;
create policy "sensor_data_insert_device"
  on public.sensor_data
  for insert
  to anon
  with check (
    temperature between -40 and 125
    and humidity between 0 and 100
    and air_quality between 0 and 4095
  );

create or replace function public.sensor_data_rate_limit()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if current_user = 'anon'
     and exists (
       select 1 from public.sensor_data s
       where s.created_at > now() - interval '2 seconds'
         and s.created_at <= now()
     ) then
    raise exception 'sensor_data: at most one anonymous insert per 2 seconds' using errcode = '54001';
  end if;
  return new;
end
$$;

commit;

notify pgrst, 'reload schema';
