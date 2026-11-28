-- 20261128000003_soil_and_air_ppm.sql
--
-- New readings from the ESP32 monitor (monitoring.cpp):
--   soil_moisture     soil moisture, %            capacitive soil moisture sensor (analog)
--   soil_temperature  soil temperature, deg C     DS18B20 waterproof probe
--   air_ppm           air quality, ppm (CO2 eq.)  MQ-135, converted from the raw ADC value
-- The existing columns keep their meaning: temperature and humidity are the AIR temperature and
-- humidity (DHT11), air_quality is the raw MQ-135 ADC value (0..4095).
--
-- All three columns are nullable: an older firmware that does not send them keeps working, and a
-- broken sensor sends null instead of a fake number.
--
-- The device writes as anon, so anon gets INSERT on the new columns only (as in 000005), and the
-- insert policy checks their ranges too (null is allowed). Signed-in users still only read.
-- SELECT is a table-level grant (baseline), so the app and the realtime stream see the new columns
-- without another grant.
--
-- Idempotent, one transaction. Revert (manual):
--   revoke insert (soil_moisture, soil_temperature, air_ppm) on public.sensor_data from anon;
--   recreate "sensor_data_insert_device" as in 20261113000007, then
--   alter table public.sensor_data drop column soil_moisture, drop column soil_temperature,
--     drop column air_ppm;

begin;

alter table public.sensor_data
  add column if not exists soil_moisture double precision,
  add column if not exists soil_temperature double precision,
  add column if not exists air_ppm double precision;

comment on column public.sensor_data.temperature is 'Air temperature, deg C (DHT11)';
comment on column public.sensor_data.humidity is 'Air relative humidity, % (DHT11)';
comment on column public.sensor_data.air_quality is 'Raw MQ-135 ADC value, 0..4095';
comment on column public.sensor_data.soil_moisture is 'Soil moisture, % (capacitive sensor)';
comment on column public.sensor_data.soil_temperature is 'Soil temperature, deg C (DS18B20)';
comment on column public.sensor_data.air_ppm is 'Air quality, ppm CO2 equivalent (MQ-135)';

grant insert (soil_moisture, soil_temperature, air_ppm) on public.sensor_data to anon;

drop policy if exists "sensor_data_insert_device" on public.sensor_data;
create policy "sensor_data_insert_device"
  on public.sensor_data
  for insert
  to anon
  with check (
    temperature between -40 and 125
    and humidity between 0 and 100
    and air_quality between 0 and 4095
    and (soil_moisture is null or soil_moisture between 0 and 100)
    and (soil_temperature is null or soil_temperature between -55 and 125)
    and (air_ppm is null or air_ppm between 0 and 10000)
  );

commit;

notify pgrst, 'reload schema';
