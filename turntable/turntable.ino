// ArchoAI turntable: Arduino Uno + CNC Shield V3 + NEMA 17 (stepper driver in any of the X/Y/Z slots).
// The motor needs its own 12-24 V supply on the shield's power terminal; USB only powers the Arduino.
//
// Serial protocol, 115200 baud, one command per line, every answer is one line:
//   PING           -> "ARCHOAI-TURNTABLE 1 steps=<steps per turn>"
//   ROT <degrees>  -> turns the table (negative = other direction), then "OK ROT <degrees>"
//   STEPS <n>      -> sets the motor steps for one full turn of the table and saves it (EEPROM),
//                     "OK STEPS <n>". 200 = full steps; with all three microstep jumpers under the
//                     driver (1/16) it is 3200; with a belt or gear multiply by its ratio.
//   SPEED <rpm>    -> cruise speed in table turns per minute (1..60, default 22), "OK SPEED <rpm>"
//   RAMP <seconds> -> time to speed up / slow down (0.05..5, default 0.3), "OK RAMP <seconds>"
//   PROFILE SCURVE|LINEAR -> speed eases in and out (default), or constant acceleration
//   HOLD ON|OFF    -> keep the motor current on between moves (default OFF: silent at standstill)
//   AXIS X|Y|Z|ALL -> which driver slot(s) of the shield move (default ALL: the motor works in any slot)
//   OFF            -> motor current off (the table turns freely), "OK OFF"
// Errors answer "ERR <reason>".
//
// Build and flash from the Pi: bash turntable/flash.sh  (or open this file in the Arduino IDE).
#include <EEPROM.h>

// CNC Shield V3 driver slots: X, Y, Z (step pins 2, 3, 4; direction pins 5, 6, 7).
const uint8_t STEP_PINS[3] = {2, 3, 4};
const uint8_t DIR_PINS[3] = {5, 6, 7};
const uint8_t ENABLE_PIN = 8;  // all drivers, active LOW
uint8_t axisMask = 0b111;      // which slots are driven: all three by default, AXIS X|Y|Z|ALL to change

const uint16_t EEPROM_MAGIC = 0xA7C1;
long stepsPerTurn = 200;
// Defaults chosen by measuring the motor noise with the webcam microphone (full steps, 200 per turn):
// 12 rpm with a 0.4 s constant-acceleration ramp was the quietest of everything tried, near the room
// background (1.2 s per 60 degrees). Slower (3-6 rpm) and faster (25-30 rpm) ring louder, and the
// S-curve is louder at every speed. Microstepping jumpers (then STEPS 3200 / 6400) make it silent.
// Defaults measured with a microphone on this turntable (full steps, 2026-11-28): S-curve at 22 rpm with a
// 0.3 s ramp made the least noise per 60 degree move (about 3 dB less than linear at 12 rpm).
float rpm = 22.0;          // cruise speed (table turns per minute)
float rampSeconds = 0.3;   // time to reach the cruise speed (and to stop)
bool sCurve = true;        // true: speed eases in and out (S-curve), false: constant acceleration
bool holdAfterMove = false;  // false: motor current off between moves (silent, cool)

void saveSteps() {
  EEPROM.put(0, EEPROM_MAGIC);
  EEPROM.put(2, stepsPerTurn);
}

void loadSteps() {
  uint16_t magic;
  EEPROM.get(0, magic);
  long stored;
  EEPROM.get(2, stored);
  if (magic == EEPROM_MAGIC && stored >= 50 && stored <= 200000) stepsPerTurn = stored;
}

// Moves a number of steps with a smooth trapezoid profile: constant acceleration up to the cruise
// speed, constant deceleration at the end (v = sqrt(2 a s) from each end). Steps are timed with
// micros(), so the speed changes gently from step to step: smoother and quieter than a fixed rate.
void moveSteps(long steps) {
  for (uint8_t a = 0; a < 3; a++) digitalWrite(DIR_PINS[a], steps >= 0 ? HIGH : LOW);
  long count = steps >= 0 ? steps : -steps;
  if (count == 0) return;
  digitalWrite(ENABLE_PIN, LOW);
  delay(30);  // let the driver energize the coils before the first step

  float vmax = rpm * stepsPerTurn / 60.0;  // steps per second
  if (vmax > 4000) vmax = 4000;
  float accel = vmax / rampSeconds;         // steps per second squared
  unsigned long next = micros();
  for (long i = 0; i < count; i++) {
    long fromEdge = min(i, count - 1 - i) + 1;
    float v;
    if (sCurve) {
      // Raised-cosine speed over the ramp distance: no sudden change of acceleration (jerk) at the
      // start and the end of the ramp, which is what makes a stepper knock.
      float rampSteps = vmax * rampSeconds / 2.0;  // distance covered while speeding up
      float x = rampSteps > 0 ? fromEdge / rampSteps : 1.0;
      if (x > 1.0) x = 1.0;
      v = vmax * (0.12 + 0.88 * 0.5 * (1.0 - cos(PI * x)));
    } else {
      v = sqrt(2.0 * accel * fromEdge);
      if (v > vmax) v = vmax;
    }
    while ((long)(micros() - next) < 0) {
    }
    for (uint8_t a = 0; a < 3; a++) if (axisMask & (1 << a)) digitalWrite(STEP_PINS[a], HIGH);
    delayMicroseconds(4);
    for (uint8_t a = 0; a < 3; a++) digitalWrite(STEP_PINS[a], LOW);
    next += (unsigned long)(1000000.0 / v);
  }
  if (!holdAfterMove) {
    delay(150);                       // let the rotor settle on the last step
    digitalWrite(ENABLE_PIN, HIGH);   // no current at standstill: no hum, no heat
  }
}

void setup() {
  for (uint8_t a = 0; a < 3; a++) {
    pinMode(STEP_PINS[a], OUTPUT);
    pinMode(DIR_PINS[a], OUTPUT);
  }
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(ENABLE_PIN, HIGH);  // motor off until the first move
  loadSteps();
  Serial.begin(115200);
  Serial.print("ARCHOAI-TURNTABLE 1 steps=");
  Serial.println(stepsPerTurn);
}

void loop() {
  if (!Serial.available()) return;
  String line = Serial.readStringUntil('\n');
  line.trim();
  if (line.length() == 0) return;
  int space = line.indexOf(' ');
  String cmd = space < 0 ? line : line.substring(0, space);
  String arg = space < 0 ? "" : line.substring(space + 1);
  cmd.toUpperCase();

  if (cmd == "PING") {
    Serial.print("ARCHOAI-TURNTABLE 1 steps=");
    Serial.println(stepsPerTurn);
  } else if (cmd == "ROT") {
    float degrees = arg.toFloat();
    if (arg.length() == 0 || degrees < -3600 || degrees > 3600) {
      Serial.println("ERR ROT needs degrees between -3600 and 3600");
      return;
    }
    moveSteps(lround(degrees * stepsPerTurn / 360.0));
    Serial.print("OK ROT ");
    Serial.println(arg);
  } else if (cmd == "STEPS") {
    long value = arg.toInt();
    if (value < 50 || value > 200000) {
      Serial.println("ERR STEPS must be 50..200000");
      return;
    }
    stepsPerTurn = value;
    saveSteps();
    Serial.print("OK STEPS ");
    Serial.println(stepsPerTurn);
  } else if (cmd == "SPEED") {
    float value = arg.toFloat();
    if (value < 1 || value > 60) {
      Serial.println("ERR SPEED must be 1..60 rpm");
      return;
    }
    rpm = value;
    Serial.print("OK SPEED ");
    Serial.println(arg);
  } else if (cmd == "RAMP") {
    float value = arg.toFloat();
    if (value < 0.05 || value > 5) {
      Serial.println("ERR RAMP must be 0.05..5 seconds");
      return;
    }
    rampSeconds = value;
    Serial.print("OK RAMP ");
    Serial.println(arg);
  } else if (cmd == "PROFILE") {
    arg.toUpperCase();
    if (arg != "SCURVE" && arg != "LINEAR") {
      Serial.println("ERR PROFILE must be SCURVE or LINEAR");
      return;
    }
    sCurve = arg == "SCURVE";
    Serial.print("OK PROFILE ");
    Serial.println(arg);
  } else if (cmd == "HOLD") {
    arg.toUpperCase();
    if (arg != "ON" && arg != "OFF") {
      Serial.println("ERR HOLD must be ON or OFF");
      return;
    }
    holdAfterMove = arg == "ON";
    if (!holdAfterMove) digitalWrite(ENABLE_PIN, HIGH);
    Serial.print("OK HOLD ");
    Serial.println(arg);
  } else if (cmd == "AXIS") {
    arg.toUpperCase();
    if (arg == "X") axisMask = 0b001;
    else if (arg == "Y") axisMask = 0b010;
    else if (arg == "Z") axisMask = 0b100;
    else if (arg == "ALL") axisMask = 0b111;
    else {
      Serial.println("ERR AXIS must be X, Y, Z or ALL");
      return;
    }
    Serial.print("OK AXIS ");
    Serial.println(arg);
  } else if (cmd == "OFF") {
    digitalWrite(ENABLE_PIN, HIGH);
    Serial.println("OK OFF");
  } else {
    Serial.println("ERR unknown command");
  }
}
