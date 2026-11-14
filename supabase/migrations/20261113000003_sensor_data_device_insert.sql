-- 20261113000003_sensor_data_device_insert.sql
--
-- Lets the ESP32 firmware insert readings again. SECURITY TRADE-OFF, kept in its own file so it can
-- be reverted on its own: the firmware writes with the publishable (anon) key, and that key is
-- public (it is committed in the repository). Anyone who has it can insert rows that pass the range
-- check. Mitigations here: value ranges in the policy (air_quality allows the raw ADC range 0..4095
-- the firmware sends), and a rate cap of one insert per 2 seconds for the anon role.
--
-- Before this migration public.sensor_data had only a SELECT policy, so the firmware's POSTs were
-- rejected by RLS.
--
-- Revert (one statement block):
--   drop trigger if exists sensor_data_rate_limit_trg on public.sensor_data;
--   drop function if exists public.sensor_data_rate_limit();
--   drop policy if exists sensor_data_insert_device on public.sensor_data;

begin;

drop policy if exists "sensor_data_insert_device" on public.sensor_data;
create policy "sensor_data_insert_device"
  on public.sensor_data
  for insert
  to anon, authenticated
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
       select 1 from public.sensor_data s where s.created_at > now() - interval '2 seconds'
     ) then
    raise exception 'sensor_data: at most one anonymous insert per 2 seconds' using errcode = '54001';
  end if;
  return new;
end
$$;

drop trigger if exists sensor_data_rate_limit_trg on public.sensor_data;
create trigger sensor_data_rate_limit_trg
  before insert on public.sensor_data
  for each row execute function public.sensor_data_rate_limit();

commit;

notify pgrst, 'reload schema';
