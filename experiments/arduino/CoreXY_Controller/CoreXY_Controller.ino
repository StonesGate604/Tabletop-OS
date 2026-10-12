#include <Arduino.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// UNO + CNC Shield V3: motor A in X socket, motor B in Y socket.
const uint8_t A_STEP = 2, B_STEP = 3, A_DIR = 5, B_DIR = 6, ENABLE_PIN = 8;
const bool INVERT_A = false, INVERT_B = false;
// EXAMPLE ONLY: 200 full steps/rev, 16 microsteps, GT2 belt, 20 teeth.
const float STEPS_PER_MM = 200.0f * 16.0f / (2.0f * 20.0f);
// Commissioning envelope around a manually selected central origin.
const float X_MIN = -50, X_MAX = 50, Y_MIN = -50, Y_MAX = 50;
const float MAX_SPEED = 10.0f;       // Cartesian mm/s
const float ACCELERATION = 30.0f;   // Cartesian mm/s^2
const float MAX_EVENT_RATE = 1500;  // conservative software pulse rate
// Optional NO switches to GND; triggered LOW. No automatic homing.
const bool USE_LIMITS = false;
const uint8_t X_LIMIT = 9, Y_LIMIT = 10;

long posA = 0, posB = 0;
bool enabled = false, referenced = false;
char line[80];
uint8_t lineLength = 0;
bool discardLine = false;

float xPosition() { return (posA + posB) / (2.0f * STEPS_PER_MM); }
float yPosition() { return (posA - posB) / (2.0f * STEPS_PER_MM); }
bool limitHit() {
  return USE_LIMITS && (digitalRead(X_LIMIT) == LOW || digitalRead(Y_LIMIT) == LOW);
}
void disableMotors() {
  digitalWrite(ENABLE_PIN, HIGH);
  enabled = false;
  referenced = false; // carriage may move when power is removed
}
void stopMotion() {
  disableMotors();
  lineLength = 0;
  discardLine = true; // discard rest of interrupted command until newline
  Serial.println(F("STOP: position untrusted; ENABLE then ZERO required"));
}
void reportPosition() {
  Serial.print(F("X=")); Serial.print(xPosition(), 3);
  Serial.print(F(" Y=")); Serial.print(yPosition(), 3);
  Serial.print(F(" enabled=")); Serial.print(enabled);
  Serial.print(F(" referenced=")); Serial.println(referenced);
}

// Busy-time input is discarded, except ! which stops at once.
bool pollMotion() {
  if (limitHit()) { stopMotion(); return false; }
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '!') { stopMotion(); return false; }
    if (c == '\n' || c == '\r') discardLine = false;
    else discardLine = true;
  }
  return true;
}

bool moveTo(float x, float y, float speed) {
  if (!enabled || !referenced) {
    Serial.println(F("ERR: ENABLE then ZERO first")); return false;
  }
  if (!isfinite(x) || !isfinite(y) || !isfinite(speed) ||
      x < X_MIN || x > X_MAX || y < Y_MIN || y > Y_MAX ||
      speed < 0.1f || speed > MAX_SPEED) {
    Serial.println(F("ERR: target or speed outside configured range")); return false;
  }
  long targetA = lroundf((x + y) * STEPS_PER_MM);
  long targetB = lroundf((x - y) * STEPS_PER_MM);
  float quantizedX = (targetA + targetB) / (2.0f * STEPS_PER_MM);
  float quantizedY = (targetA - targetB) / (2.0f * STEPS_PER_MM);
  if (quantizedX < X_MIN || quantizedX > X_MAX || quantizedY < Y_MIN || quantizedY > Y_MAX) {
    Serial.println(F("ERR: rounded target outside limits")); return false;
  }
  long da = targetA - posA, db = targetB - posB;
  long na = labs(da), nb = labs(db), events = na > nb ? na : nb;
  if (!events) return true;
  float dx = quantizedX - xPosition(), dy = quantizedY - yPosition();
  float distance = sqrtf(dx * dx + dy * dy);
  float ds = distance / events;
  float topSpeed = fminf(speed, MAX_EVENT_RATE * ds);
  int8_t signA = da >= 0 ? 1 : -1, signB = db >= 0 ? 1 : -1;
  digitalWrite(A_DIR, (da >= 0) ^ INVERT_A);
  digitalWrite(B_DIR, (db >= 0) ^ INVERT_B);
  delayMicroseconds(10);
  long errorA = events / 2, errorB = events / 2;
  for (long i = 0; i < events; ++i) {
    // Midpoint approximation of a triangular/trapezoidal speed profile.
    float fromStart = (i + 0.5f) * ds;
    float toEnd = (events - i - 0.5f) * ds;
    float v = fminf(topSpeed, sqrtf(2 * ACCELERATION * fminf(fromStart, toEnd)));
    unsigned long interval = (unsigned long)(1000000.0f * ds / v);
    unsigned long started = micros();
    do {
      if (!pollMotion()) return false;
    } while ((unsigned long)(micros() - started) < interval);
    errorA += na; errorB += nb;
    bool stepA = errorA >= events, stepB = errorB >= events;
    if (stepA) { errorA -= events; digitalWrite(A_STEP, HIGH); posA += signA; }
    if (stepB) { errorB -= events; digitalWrite(B_STEP, HIGH); posB += signB; }
    delayMicroseconds(5);
    digitalWrite(A_STEP, LOW); digitalWrite(B_STEP, LOW);
  }
  return true;
}

bool parseNumber(char *s, float &value) {
  if (!s || !*s) return false;
  char *end;
  value = strtod(s, &end);
  return *end == '\0' && isfinite(value);
}

void command(char *input) {
  char *tokens[5];
  uint8_t count = 0;
  char *t = strtok(input, " \t");
  while (t && count < 5) { tokens[count++] = t; t = strtok(NULL, " \t"); }
  if (!count) return;
  if (count == 1) {
    if (!strcmp(tokens[0], "ENABLE")) {
      if (limitHit()) { Serial.println(F("ERR: limit active")); return; }
      digitalWrite(ENABLE_PIN, LOW); enabled = true;
      delay(2); Serial.println(F("OK ENABLE")); return;
    }
    if (!strcmp(tokens[0], "DISABLE")) {
      disableMotors(); Serial.println(F("OK DISABLE; origin invalidated")); return;
    }
    if (!strcmp(tokens[0], "ZERO")) {
      if (!enabled || limitHit()) { Serial.println(F("ERR: enable and clear limits first")); return; }
      posA = posB = 0; referenced = true;
      Serial.println(F("OK ZERO (manual origin, no movement)")); return;
    }
    if (!strcmp(tokens[0], "POS")) { reportPosition(); return; }
  }
  if (count == 4 && (!strcmp(tokens[0], "MOVE") || !strcmp(tokens[0], "JOG"))) {
    float x, y, speed;
    if (!parseNumber(tokens[1], x) || !parseNumber(tokens[2], y) || !parseNumber(tokens[3], speed)) {
      Serial.println(F("ERR: invalid number")); return;
    }
    if (!strcmp(tokens[0], "JOG")) { x += xPosition(); y += yPosition(); }
    if (moveTo(x, y, speed)) { Serial.println(F("OK MOVE")); reportPosition(); }
    return;
  }
  Serial.println(F("ERR: ENABLE | ZERO | MOVE x y mm/s | JOG dx dy mm/s | POS | DISABLE | !"));
}

void setup() {
  digitalWrite(ENABLE_PIN, HIGH); pinMode(ENABLE_PIN, OUTPUT);
  pinMode(A_STEP, OUTPUT); pinMode(B_STEP, OUTPUT);
  digitalWrite(A_STEP, LOW); digitalWrite(B_STEP, LOW);
  pinMode(A_DIR, OUTPUT); pinMode(B_DIR, OUTPUT);
  if (USE_LIMITS) { pinMode(X_LIMIT, INPUT_PULLUP); pinMode(Y_LIMIT, INPUT_PULLUP); }
  Serial.begin(115200);
  Serial.println(F("CoreXY ready, motors disabled. Manual origin required."));
}

void loop() {
  if (enabled && limitHit()) stopMotion();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '!') { stopMotion(); continue; }
    if (c == '\r' || c == '\n') {
      if (!discardLine && lineLength) {
        line[lineLength] = '\0'; command(line);
      }
      lineLength = 0; discardLine = false;
    } else if (!discardLine) {
      if (lineLength < sizeof(line) - 1) line[lineLength++] = c;
      else { lineLength = 0; discardLine = true; Serial.println(F("ERR: line too long")); }
    }
  }
}
