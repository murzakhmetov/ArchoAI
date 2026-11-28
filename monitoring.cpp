#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <OneWire.h>
#include <DallasTemperature.h>
#include <math.h>

// === Config ===
// The board must join the same Wi-Fi network as the Raspberry Pi, which is the server now
// (database and storage on the Pi, see server/README.md).
#define WIFI_SSID      "HONOR X8d"
#define WIFI_PASSWORD  "Alikhan12345"
// ArchoAI server on the Raspberry Pi: http://<pi address>:54321 (server/setup.sh prints it).
// Give the Pi a fixed address in the router (DHCP reservation), otherwise it can change.
// Cloud only (no Pi): put "https://<ref>.supabase.co/rest/v1/sensor_data" here and the cloud
// project's publishable key below. Pi AND cloud: keep the Pi here; server/replicate.py on the Pi
// copies the readings to the cloud (server/README.md). Keep scripts/monitoring.cpp identical.
#define SUPABASE_URL   "http://10.21.2.23:54321/rest/v1/sensor_data"
// Publishable key of the Pi server (SUPABASE_KEY in archoai_app/.env). It only allows sensor
// readings within the value ranges, at most one every 2 seconds.
#define SUPABASE_KEY   "sb_publishable_D4bdWy0AwSGBhrBZfSrLAg_ZT9AKlWY"

#define WIFI_CONNECT_TIMEOUT_MS     15000  // setup(): stop waiting for WiFi after 15 s and continue offline
#define WIFI_RECONNECT_INTERVAL_MS  10000  // loop(): retry a dropped WiFi connection at most every 10 s

// === Sensors and wiring (ESP32 DevKit) ===
//   DHT11                  air temperature + air humidity    DATA -> GPIO26
//   MQ-135                 air quality (ppm, CO2 equivalent)  AO   -> GPIO34
//   capacitive soil sensor soil moisture, %                   AOUT -> GPIO35 (3.3 V supply)
//   DS18B20 (waterproof)   soil temperature, deg C            DATA -> GPIO4, 4.7 kOhm DATA..3.3 V
//   SSD1306 OLED 128x64    SDA -> GPIO21, SCL -> GPIO22
// GPIO34/35 are ADC1 pins: ADC2 pins do not work while WiFi is on.
#define GAS_PIN        34
#define SOIL_PIN       35
#define ONE_WIRE_PIN   4
#define DHT_PIN        26
#define SDA_PIN        21
#define SCL_PIN        22

// --- MQ-135 -> ppm ---------------------------------------------------------------------------------
// Rs = RL * (Vc - Vout) / Vout, ppm = A * (Rs / R0) ^ B (CO2 curve of the MQ-135 datasheet).
// MQ_VC: heater/circuit supply of the module. MQ_DIVIDER: if AO goes through a voltage divider into the
// ESP32 (needed with a 5 V module, AO can reach 5 V), the factor it divides by (2.0 for 10k/10k).
// R0 is the sensor's resistance in clean air and differs from one sensor to the next: after 24-48 h of
// warm-up, leave the board outdoors for a few minutes and copy the "R0 for 420 ppm" value from the
// serial monitor into MQ135_R0_KOHM.
#define MQ_VC            5.0f
#define MQ_DIVIDER       2.0f
#define MQ_RL_KOHM       10.0f   // load resistor on the module (often marked 102 = 1 kOhm, 103 = 10 kOhm)
#define MQ135_R0_KOHM    76.63f
#define MQ135_A          116.6020682f
#define MQ135_B          -2.769034857f
#define CLEAN_AIR_PPM    420.0f

// --- Capacitive soil moisture sensor -> % ----------------------------------------------------------
// Raw ADC value in dry air (0 %) and in a glass of water (100 %). Check them once with the serial
// monitor and put your own numbers here.
#define SOIL_RAW_DRY     3000
#define SOIL_RAW_WET     1300

Adafruit_SSD1306 display(128, 64, &Wire, -1);
DHT dht(DHT_PIN, DHT11);
OneWire oneWire(ONE_WIRE_PIN);
DallasTemperature soilProbe(&oneWire);

// Time of the last WiFi reconnect attempt. setup() starts the clock when it ends, so loop() does not
// call WiFi.reconnect() at once and interrupt a connection that is still being established.
static unsigned long lastReconnectAttempt = 0;

static int readAverage(int pin) {
  long sum = 0;
  for (int i = 0; i < 8; i++) { sum += analogRead(pin); delay(5); }
  return (int)(sum / 8);
}

// MQ-135 ppm, or NAN when the sensor gives no usable voltage (not connected, still cold).
static float readAirPpm(int raw, float *rsOut) {
  float vout = analogReadMilliVolts(GAS_PIN) / 1000.0f * MQ_DIVIDER;
  *rsOut = NAN;
  if (raw <= 0 || vout <= 0.05f || vout >= MQ_VC) return NAN;
  float rs = MQ_RL_KOHM * (MQ_VC - vout) / vout;
  *rsOut = rs;
  float ppm = MQ135_A * powf(rs / MQ135_R0_KOHM, MQ135_B);
  if (!isfinite(ppm)) return NAN;
  return constrain(ppm, 0.0f, 10000.0f);
}

// Soil moisture %, or NAN when the pin reads a rail (sensor not connected).
static float readSoilMoisture(int raw) {
  if (raw <= 10 || raw >= 4090) return NAN;
  float pct = (float)(SOIL_RAW_DRY - raw) * 100.0f / (float)(SOIL_RAW_DRY - SOIL_RAW_WET);
  return constrain(pct, 0.0f, 100.0f);
}

// Soil temperature from the DS18B20, or NAN when the probe does not answer.
static float readSoilTemperature() {
  soilProbe.requestTemperatures();
  float t = soilProbe.getTempCByIndex(0);
  if (t == DEVICE_DISCONNECTED_C || t < -55.0f || t > 125.0f) return NAN;
  return t;
}

// A JSON number, or null for a missing reading (the database stores null, not a fake 0).
static String jsonNumber(float value, int decimals) {
  return isnan(value) ? String("null") : String(value, decimals);
}

static String shown(float value, int decimals, const char *unit) {
  return isnan(value) ? String("--") : String(value, decimals) + unit;
}

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);
  analogSetAttenuation(ADC_11db);  // ADC range up to about 3.1 V
  dht.begin();
  soilProbe.begin();
  soilProbe.setResolution(12);
  delay(2000);

  display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  display.clearDisplay();
  display.setTextColor(SSD1306_WHITE);

  // Connect WiFi
  display.setCursor(0, 0); display.print("Connecting WiFi...");
  display.display();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  // Wait for the connection, but not forever: without WiFi the sensors are still shown offline
  unsigned long wifiStart = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - wifiStart < WIFI_CONNECT_TIMEOUT_MS) {
    delay(500);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println("\nWiFi OK: " + WiFi.localIP().toString());
  } else {
    Serial.println("\nWiFi connect timed out, continuing in offline mode");
  }
  lastReconnectAttempt = millis();
}

void loop() {
  // Restore a dropped WiFi connection (does not block, at most one attempt per interval)
  if (WiFi.status() != WL_CONNECTED && millis() - lastReconnectAttempt >= WIFI_RECONNECT_INTERVAL_MS) {
    lastReconnectAttempt = millis();
    Serial.println("WiFi lost, reconnecting...");
    WiFi.reconnect();
  }

  // Air (DHT11)
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();
  bool dhtOk = !isnan(temp) && !isnan(hum);

  // Air quality (MQ-135): raw ADC average (column air_quality) and ppm (column air_ppm)
  int gasRaw = readAverage(GAS_PIN);
  float rs;
  float airPpm = readAirPpm(gasRaw, &rs);

  // Soil
  int soilRaw = readAverage(SOIL_PIN);
  float soilMoisture = readSoilMoisture(soilRaw);
  float soilTemp = readSoilTemperature();

  Serial.printf("[sensors] air %.1fC %.1f%% | gas raw %d ppm %.0f (Rs %.1fk, R0 for %.0f ppm: %.1fk) | soil raw %d %.0f%% %.2fC\n",
                temp, hum, gasRaw, airPpm, rs, CLEAN_AIR_PPM,
                isnan(rs) ? NAN : rs / powf(CLEAN_AIR_PPM / MQ135_A, 1.0f / MQ135_B),
                soilRaw, soilMoisture, soilTemp);

  // --- OLED ---
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.print("ArchoAI | ");
  display.print(WiFi.status() == WL_CONNECTED ? "WiFi OK" : "No WiFi");
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

  display.setCursor(0, 12); display.print("Air T : " + (dhtOk ? String(temp, 1) + " C" : String("ERR")));
  display.setCursor(0, 21); display.print("Air H : " + (dhtOk ? String(hum, 1) + " %" : String("ERR")));
  display.setCursor(0, 30); display.print("Air Q : " + shown(airPpm, 0, " ppm"));
  display.setCursor(0, 39); display.print("Soil T: " + shown(soilTemp, 1, " C"));
  display.setCursor(0, 48); display.print("Soil M: " + shown(soilMoisture, 0, " %"));

  display.setCursor(0, 57);
  display.print(WiFi.status() == WL_CONNECTED ? "Sending data..." : "Offline mode");

  display.display();

  // --- Supabase ---
  // The air temperature and humidity are required by the database; a missing soil or ppm value goes
  // out as null.
  if (dhtOk && WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SUPABASE_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("apikey", SUPABASE_KEY);
    http.addHeader("Authorization", "Bearer " + String(SUPABASE_KEY));

    String body = "{\"temperature\":"      + String(temp, 1) +
                  ",\"humidity\":"         + String(hum, 1)  +
                  ",\"air_quality\":"      + String((float)gasRaw, 1) +
                  ",\"air_ppm\":"          + jsonNumber(airPpm, 0) +
                  ",\"soil_moisture\":"    + jsonNumber(soilMoisture, 1) +
                  ",\"soil_temperature\":" + jsonNumber(soilTemp, 2) + "}";

    int code = http.POST(body);
    Serial.printf("[Supabase] %d | %s\n", code, body.c_str());
    if (code < 200 || code >= 300) {
      if (code < 0) {
        // Negative values are HTTPClient transport errors (no connection, timeout, ...), not HTTP statuses
        Serial.printf("[Supabase] ERROR: request failed: %s\n", http.errorToString(code).c_str());
      } else {
        // Non-2xx HTTP status: show the server's answer (RLS, auth or schema problems appear here)
        Serial.printf("[Supabase] ERROR: HTTP %d: %s\n", code, http.getString().c_str());
      }
    }
    http.end();
  }

  delay(5000);
}
