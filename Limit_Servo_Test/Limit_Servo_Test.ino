#include <Arduino.h>
#include <Servo.h>
#include <stdlib.h>
#include <string.h>

// UNO R3 + CNC Shield V3. Confirm the physical header mapping on your board.
const uint8_t X_LIMIT_PIN = 9;
const uint8_t Y_LIMIT_PIN = 10;
const uint8_t SERVO_PIN = 11;
const uint8_t ENABLE_PIN = 8;
// Bare mechanical switches: COM -> GND, NO -> input.
const bool SWITCH_NORMALLY_CLOSED = false;
const unsigned long DEBOUNCE_MS = 25;
const unsigned long SERVO_TIMEOUT_MS = 10000;
// TD-9412MG datasheet range. Actual travel depends on the purchased variant.
// Test unloaded, expanding from center before trying either endpoint.
const int SERVO_MIN_ANGLE = 0;
const int SERVO_MAX_ANGLE = 180;
const int SERVO_MIN_US = 500;
const int SERVO_MAX_US = 2500;

Servo testServo;
const uint8_t limitPins[2] = {X_LIMIT_PIN, Y_LIMIT_PIN};
bool rawHit[2], stableHit[2];
unsigned long changedAt[2];
unsigned long lastServoCommand = 0;
int commandedPulseUs = 1500;
char inputLine[48];
uint8_t inputLength = 0;
bool discardLine = false;

bool readHit(uint8_t pin) {
  return digitalRead(pin) == (SWITCH_NORMALLY_CLOSED ? HIGH : LOW);
}

void printLimit(uint8_t index) {
  Serial.print(index == 0 ? F("X: ") : F("Y: "));
  Serial.println(stableHit[index] ? F("PRESSED") : F("RELEASED"));
}

void servoOff() {
  testServo.detach();
  digitalWrite(SERVO_PIN, LOW);
  Serial.println(F("SERVO OFF (signal stopped; power still connected)"));
}

void printStatus() {
  printLimit(0);
  printLimit(1);
  Serial.print(F("SERVO: "));
  if (testServo.attached()) {
    Serial.print(F("ON, commanded pulse us="));
    Serial.println(commandedPulseUs);
  } else {
    Serial.println(F("OFF"));
  }
  Serial.println(F("STEPPERS: DISABLED"));
}

void printHelp() {
  Serial.println(F("STATUS | SERVO 0..180 | US 500..2500 | OFF | HELP | !"));
  Serial.println(F("SERVO 90 starts at nominal center; test with linkage removed."));
  Serial.println(F("Servo signal turns off after 10 seconds without a new SERVO/US command."));
}

void processCommand(char *line) {
  char *cmd = strtok(line, " \t");
  if (!cmd) return;
  char *arg = strtok(NULL, " \t");
  char *extra = strtok(NULL, " \t");
  bool angleCommand = !strcmp(cmd, "SERVO");
  bool pulseCommand = !strcmp(cmd, "US");
  if ((angleCommand || pulseCommand) && arg && !extra) {
    // Bound digit count before strtol to reject overflow on the AVR's long.
    size_t digits = strlen(arg);
    if (digits > 4) { Serial.println(F("ERR: invalid number")); return; }
    for (size_t i = 0; i < digits; ++i) {
      if (arg[i] < '0' || arg[i] > '9') {
        Serial.println(F("ERR: use unsigned integer")); return;
      }
    }
    char *end;
    long value = strtol(arg, &end, 10);
    long minimum = angleCommand ? SERVO_MIN_ANGLE : SERVO_MIN_US;
    long maximum = angleCommand ? SERVO_MAX_ANGLE : SERVO_MAX_US;
    if (*end || value < minimum || value > maximum) {
      Serial.println(F("ERR: use SERVO 0..180 or US 500..2500"));
      return;
    }
    if (!testServo.attached()) {
      // Preload a neutral pulse before enabling output.
      testServo.writeMicroseconds(1500);
      if (testServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US) == INVALID_SERVO) {
        Serial.println(F("ERR: servo attach failed"));
        return;
      }
    }
    commandedPulseUs = angleCommand
      ? (int)(SERVO_MIN_US + value * (SERVO_MAX_US - SERVO_MIN_US) / 180L)
      : (int)value;
    testServo.writeMicroseconds(commandedPulseUs);
    lastServoCommand = millis();
    Serial.print(angleCommand ? F("OK SERVO ") : F("OK US "));
    Serial.print(value);
    Serial.print(F("; pulse us="));
    Serial.println(commandedPulseUs);
    return;
  }
  if (!arg && !strcmp(cmd, "STATUS")) { printStatus(); return; }
  if (!arg && !strcmp(cmd, "OFF")) { servoOff(); return; }
  if (!arg && !strcmp(cmd, "HELP")) { printHelp(); return; }
  Serial.println(F("ERR: unknown command; type HELP"));
}

void setup() {
  // Disable all stepper drivers and hold STEP outputs low.
  digitalWrite(ENABLE_PIN, HIGH);
  pinMode(ENABLE_PIN, OUTPUT);
  for (uint8_t pin = 2; pin <= 4; ++pin) {
    digitalWrite(pin, LOW);
    pinMode(pin, OUTPUT);
  }
  digitalWrite(SERVO_PIN, LOW);
  pinMode(SERVO_PIN, OUTPUT);
  for (uint8_t i = 0; i < 2; ++i) {
    pinMode(limitPins[i], INPUT_PULLUP);
    rawHit[i] = stableHit[i] = readHit(limitPins[i]);
    changedAt[i] = millis();
  }
  Serial.begin(115200);
  Serial.println(F("Limit + servo test ready. Servo output OFF."));
  printHelp();
  printStatus();
}

void loop() {
  unsigned long now = millis();
  for (uint8_t i = 0; i < 2; ++i) {
    bool hit = readHit(limitPins[i]);
    if (hit != rawHit[i]) { rawHit[i] = hit; changedAt[i] = now; }
    if (now - changedAt[i] >= DEBOUNCE_MS && stableHit[i] != rawHit[i]) {
      stableHit[i] = rawHit[i];
      printLimit(i);
    }
  }
  if (testServo.attached() && now - lastServoCommand >= SERVO_TIMEOUT_MS) {
    servoOff();
  }
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '!') {
      servoOff();
      inputLength = 0;
      discardLine = true;
    } else if (c == '\r' || c == '\n') {
      if (!discardLine && inputLength) {
        inputLine[inputLength] = '\0';
        processCommand(inputLine);
      }
      inputLength = 0;
      discardLine = false;
    } else if (!discardLine) {
      if (inputLength < sizeof(inputLine) - 1) inputLine[inputLength++] = c;
      else {
        inputLength = 0;
        discardLine = true;
        Serial.println(F("ERR: line too long"));
      }
    }
  }
}
