#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <WiFi.h>
#include <HTTPClient.h>

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
#define SUPABASE_URL   "http://192.168.1.61:54321/rest/v1/sensor_data"
// Publishable key of the Pi server (SUPABASE_KEY in archoai_app/.env). It only allows sensor
// readings within the value ranges, at most one every 2 seconds.
#define SUPABASE_KEY   "sb_publishable_D4bdWy0AwSGBhrBZfSrLAg_ZT9AKlWY"

#define WIFI_CONNECT_TIMEOUT_MS     15000  // setup(): stop waiting for WiFi after 15 s and continue offline
#define WIFI_RECONNECT_INTERVAL_MS  10000  // loop(): retry a dropped WiFi connection at most every 10 s

#define GAS_PIN   34
#define DHT_PIN   26
#define SDA_PIN   21
#define SCL_PIN   22

Adafruit_SSD1306 display(128, 64, &Wire, -1);
DHT dht(DHT_PIN, DHT11);

// Time of the last WiFi reconnect attempt. setup() starts the clock when it ends, so loop() does not
// call WiFi.reconnect() at once and interrupt a connection that is still being established.
static unsigned long lastReconnectAttempt = 0;

void setup() {
  Serial.begin(115200);
  Wire.begin(SDA_PIN, SCL_PIN);
  dht.begin();
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

  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  // Gas sensor
  long sum = 0;
  for (int i = 0; i < 8; i++) { sum += analogRead(GAS_PIN); delay(5); }
  float ppm = sum / 8.0f;  // sum is long: plain "/ 8" would truncate the average

  bool dhtOk = !isnan(temp) && !isnan(hum);

  // --- OLED ---
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);

  display.setCursor(0, 0);
  display.print("ArchoAI | ");
  display.print(WiFi.status() == WL_CONNECTED ? "WiFi OK" : "No WiFi");
  display.drawLine(0, 9, 127, 9, SSD1306_WHITE);

  display.setCursor(0, 12); display.print("TEMP:");
  display.setTextSize(2);
  display.setCursor(36, 10);
  display.print(dhtOk ? String(temp, 1) + "C" : "ERR");

  display.setTextSize(1);
  display.setCursor(0, 28); display.print("HUM: ");
  display.setTextSize(2);
  display.setCursor(36, 26);
  display.print(dhtOk ? String(hum, 1) + "%" : "ERR");

  display.setTextSize(1);
  display.drawLine(0, 42, 127, 42, SSD1306_WHITE);
  display.setCursor(0, 45);
  display.print("AIR Q: " + String((int)ppm) + " raw");

  display.drawLine(0, 54, 127, 54, SSD1306_WHITE);
  display.setCursor(0, 57);
  display.print(WiFi.status() == WL_CONNECTED ? "Sending data..." : "Offline mode");

  display.display();

  // --- Supabase ---
  if (dhtOk && WiFi.status() == WL_CONNECTED) {
    HTTPClient http;
    http.begin(SUPABASE_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("apikey", SUPABASE_KEY);
    http.addHeader("Authorization", "Bearer " + String(SUPABASE_KEY));

    String body = "{\"temperature\":" + String(temp, 1) +
                  ",\"humidity\":"    + String(hum, 1)  +
                  ",\"air_quality\":" + String(ppm, 1)  + "}";

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