#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <DHT.h>
#include <WiFi.h>
#include <HTTPClient.h>

// === Config ===
#define WIFI_SSID      "HONOR X8d"
#define WIFI_PASSWORD  "Alikhan12345"
#define SUPABASE_URL   "https://wvhdhfddtusppjmsgmvn.supabase.co/rest/v1/sensor_data"
#define SUPABASE_KEY   "sb_publishable_rHDFfoFz_v9zDNKk9o64Gw_Pc-eZvln"

#define GAS_PIN   34
#define DHT_PIN   26
#define SDA_PIN   21
#define SCL_PIN   22

Adafruit_SSD1306 display(128, 64, &Wire, -1);
DHT dht(DHT_PIN, DHT11);

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
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nWiFi OK: " + WiFi.localIP().toString());
}

void loop() {
  float temp = dht.readTemperature();
  float hum  = dht.readHumidity();

  // Gas sensor
  long sum = 0;
  for (int i = 0; i < 8; i++) { sum += analogRead(GAS_PIN); delay(5); }
  float ppm = sum / 8;

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
    http.end();
  }

  delay(5000);
}