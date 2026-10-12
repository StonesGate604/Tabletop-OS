#include <Arduino.h>
#include <Servo.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// Arduino UNO R3 + common CNC Shield V3 mapping used by this project.
// Motor A is in the X driver socket; motor B is in the Y driver socket.
const uint8_t A_STEP_PIN = 2;
const uint8_t B_STEP_PIN = 3;
const uint8_t A_DIR_PIN = 5;
const uint8_t B_DIR_PIN = 6;
const uint8_t ENABLE_PIN = 8;  // Active LOW, shared by all stepper drivers.
const uint8_t X_LIMIT_PIN = 9;
const uint8_t Y_LIMIT_PIN = 10;
const uint8_t SERVO_PIN = 11;

// Change these only after the first low-speed direction test.
const bool INVERT_A = false;
const bool INVERT_B = false;

// Bare mechanical switches: COM -> GND, NO -> input. Set true for COM + NC.
const bool SWITCH_NORMALLY_CLOSED = false;
const unsigned long DEBOUNCE_MS = 25;

// Example: 200 full steps/rev, 1/16 microstep, GT2 belt, 20-tooth pulley.
// This must match the three microstep jumpers and the real transmission.
const float STEPS_PER_MM = 200.0f * 16.0f / (2.0f * 20.0f);
const float TEST_MIN_X = -50.0f;
const float TEST_MAX_X = 50.0f;
const float TEST_MIN_Y = -50.0f;
const float TEST_MAX_Y = 50.0f;
const float MAX_JOG_DISTANCE = 10.0f;  // Per command, per axis.
const float MIN_JOG_SPEED = 0.2f;
const float MAX_JOG_SPEED = 10.0f;
const float ACCELERATION = 30.0f;
const float MAX_STEP_EVENT_RATE = 1500.0f;

// Direct single-motor test limits.
const long MAX_DIRECT_STEPS = 1600;
const unsigned int MIN_DIRECT_RATE = 50;
const unsigned int MAX_DIRECT_RATE = 800;

// Servo output stops automatically. This does not remove servo power.
const int SERVO_MIN_US = 500;
const int SERVO_MAX_US = 2500;
const unsigned long SERVO_TIMEOUT_MS = 10000;

Servo testServo;
const uint8_t limitPins[2] = {X_LIMIT_PIN, Y_LIMIT_PIN};
bool rawLimit[2] = {false, false};
bool stableLimit[2] = {false, false};
unsigned long limitChangedAt[2] = {0, 0};

bool motorsEnabled = false;
bool positionReferenced = false;
long positionA = 0;
long positionB = 0;
int servoPulseUs = 1500;
unsigned long lastServoCommandAt = 0;

char inputLine[80];
uint8_t inputLength = 0;
bool discardLine = false;

bool readLimitHit(uint8_t pin) {
  return digitalRead(pin) == (SWITCH_NORMALLY_CLOSED ? HIGH : LOW);
}

bool anyLimitHit() {
  // Raw input is intentional here: stopping early is safer than waiting for
  // debounce during motion.
  return readLimitHit(X_LIMIT_PIN) || readLimitHit(Y_LIMIT_PIN);
}

float xPosition() {
  return (positionA + positionB) / (2.0f * STEPS_PER_MM);
}

float yPosition() {
  return (positionA - positionB) / (2.0f * STEPS_PER_MM);
}

void disableMotors() {
  digitalWrite(ENABLE_PIN, HIGH);
  motorsEnabled = false;
  positionReferenced = false;  // The carriage can be moved while released.
}

void servoOff(bool announce) {
  if (testServo.attached()) testServo.detach();
  digitalWrite(SERVO_PIN, LOW);
  if (announce) {
    Serial.println(F("SERVO OFF (signal stopped; servo power is still connected)"));
  }
}

void stopAll(const __FlashStringHelper *reason) {
  disableMotors();
  servoOff(false);
  inputLength = 0;
  Serial.print(F("STOP: "));
  Serial.print(reason);
  Serial.println(F("; steppers disabled, servo signal off, position untrusted"));
}

void printLimit(uint8_t index) {
  Serial.print(index == 0 ? F("X LIMIT: ") : F("Y LIMIT: "));
  Serial.println(stableLimit[index] ? F("PRESSED") : F("RELEASED"));
}

void printStatus() {
  printLimit(0);
  printLimit(1);
  Serial.print(F("STEPPERS: "));
  Serial.println(motorsEnabled ? F("ENABLED") : F("DISABLED"));
  Serial.print(F("A steps="));
  Serial.print(positionA);
  Serial.print(F("  B steps="));
  Serial.println(positionB);
  Serial.print(F("POSITION: "));
  if (positionReferenced) {
    Serial.print(F("X="));
    Serial.print(xPosition(), 3);
    Serial.print(F(" mm  Y="));
    Serial.print(yPosition(), 3);
    Serial.println(F(" mm (open-loop estimate)"));
  } else {
    Serial.println(F("UNREFERENCED; send ENABLE, then ZERO at a safe center point"));
  }
  Serial.print(F("SERVO: "));
  if (testServo.attached()) {
    Serial.print(F("ON, command pulse="));
    Serial.print(servoPulseUs);
    Serial.println(F(" us"));
  } else {
    Serial.println(F("OFF"));
  }
}

void printHelp() {
  Serial.println(F("Commands (115200 baud, newline):"));
  Serial.println(F("STATUS | HELP"));
  Serial.println(F("SERVO 0..180 | US 500..2500 | OFF"));
  Serial.println(F("ENABLE | DISABLE | ZERO"));
  Serial.println(F("MOTOR A|B signed_steps rate_steps_per_s"));
  Serial.println(F("JOG dx_mm dy_mm speed_mm_per_s"));
  Serial.println(F("STOP or !  (immediate all-output stop)"));
  Serial.println(F("During motion, other serial commands are discarded."));
}

// Poll limits and the serial emergency-stop character while a blocking move
// is in progress. Other serial input is discarded rather than queued.
bool motionSafe() {
  if (testServo.attached() &&
      (unsigned long)(millis() - lastServoCommandAt) >= SERVO_TIMEOUT_MS) {
    servoOff(true);
  }
  if (anyLimitHit()) {
    stopAll(F("LIMIT TRIGGERED"));
    return false;
  }
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '!') {
      stopAll(F("USER EMERGENCY STOP"));
      discardLine = true;
      return false;
    }
    inputLength = 0;
    discardLine = !(c == '\r' || c == '\n');
  }
  return true;
}

bool waitUntil(unsigned long startedAt, unsigned long intervalUs) {
  do {
    if (!motionSafe()) return false;
  } while ((unsigned long)(micros() - startedAt) < intervalUs);
  return true;
}

bool directMotorMove(uint8_t motorIndex, long signedSteps, unsigned int rate) {
  if (!motorsEnabled) {
    Serial.println(F("ERR: send ENABLE first"));
    return false;
  }
  if (anyLimitHit()) {
    stopAll(F("LIMIT ACTIVE"));
    return false;
  }
  if (signedSteps == 0 || labs(signedSteps) > MAX_DIRECT_STEPS ||
      rate < MIN_DIRECT_RATE || rate > MAX_DIRECT_RATE) {
    Serial.println(F("ERR: steps must be -1600..1600 except 0; rate must be 50..800"));
    return false;
  }

  uint8_t stepPin = motorIndex == 0 ? A_STEP_PIN : B_STEP_PIN;
  uint8_t dirPin = motorIndex == 0 ? A_DIR_PIN : B_DIR_PIN;
  bool invert = motorIndex == 0 ? INVERT_A : INVERT_B;
  bool positive = signedSteps > 0;
  digitalWrite(dirPin, positive ^ invert);
  delayMicroseconds(10);

  unsigned long intervalUs = 1000000UL / rate;
  long count = labs(signedSteps);
  int8_t increment = positive ? 1 : -1;
  for (long i = 0; i < count; ++i) {
    if (!motionSafe()) return false;
    unsigned long startedAt = micros();
    digitalWrite(stepPin, HIGH);
    delayMicroseconds(5);
    digitalWrite(stepPin, LOW);
    if (motorIndex == 0) positionA += increment;
    else positionB += increment;
    if (!waitUntil(startedAt, intervalUs)) return false;
  }
  return true;
}

bool jogCoreXY(float dx, float dy, float speed) {
  if (!motorsEnabled || !positionReferenced) {
    Serial.println(F("ERR: send ENABLE, then ZERO first"));
    return false;
  }
  if (!isfinite(dx) || !isfinite(dy) || !isfinite(speed) ||
      fabsf(dx) > MAX_JOG_DISTANCE || fabsf(dy) > MAX_JOG_DISTANCE ||
      (dx == 0.0f && dy == 0.0f) ||
      speed < MIN_JOG_SPEED || speed > MAX_JOG_SPEED) {
    Serial.println(F("ERR: dx/dy each within +/-10 mm and speed within 0.2..10 mm/s"));
    return false;
  }

  float targetXRequested = xPosition() + dx;
  float targetYRequested = yPosition() + dy;
  if (targetXRequested < TEST_MIN_X || targetXRequested > TEST_MAX_X ||
      targetYRequested < TEST_MIN_Y || targetYRequested > TEST_MAX_Y) {
    Serial.println(F("ERR: target is outside the +/-50 mm test envelope"));
    return false;
  }

  long targetA = lroundf((targetXRequested + targetYRequested) * STEPS_PER_MM);
  long targetB = lroundf((targetXRequested - targetYRequested) * STEPS_PER_MM);
  long deltaA = targetA - positionA;
  long deltaB = targetB - positionB;
  long stepsA = labs(deltaA);
  long stepsB = labs(deltaB);
  long events = stepsA > stepsB ? stepsA : stepsB;
  if (events == 0) return true;

  float actualTargetX = (targetA + targetB) / (2.0f * STEPS_PER_MM);
  float actualTargetY = (targetA - targetB) / (2.0f * STEPS_PER_MM);
  float actualDx = actualTargetX - xPosition();
  float actualDy = actualTargetY - yPosition();
  float distance = sqrtf(actualDx * actualDx + actualDy * actualDy);
  float distancePerEvent = distance / events;
  float topSpeed = fminf(speed, MAX_STEP_EVENT_RATE * distancePerEvent);

  int8_t incrementA = deltaA >= 0 ? 1 : -1;
  int8_t incrementB = deltaB >= 0 ? 1 : -1;
  digitalWrite(A_DIR_PIN, (deltaA >= 0) ^ INVERT_A);
  digitalWrite(B_DIR_PIN, (deltaB >= 0) ^ INVERT_B);
  delayMicroseconds(10);

  long errorA = events / 2;
  long errorB = events / 2;
  for (long event = 0; event < events; ++event) {
    if (!motionSafe()) return false;
    float fromStart = (event + 0.5f) * distancePerEvent;
    float toEnd = (events - event - 0.5f) * distancePerEvent;
    float eventSpeed = fminf(topSpeed,
                             sqrtf(2.0f * ACCELERATION * fminf(fromStart, toEnd)));
    unsigned long intervalUs = (unsigned long)(1000000.0f * distancePerEvent / eventSpeed);
    unsigned long startedAt = micros();

    errorA += stepsA;
    errorB += stepsB;
    bool pulseA = errorA >= events;
    bool pulseB = errorB >= events;
    if (pulseA) {
      errorA -= events;
      digitalWrite(A_STEP_PIN, HIGH);
      positionA += incrementA;
    }
    if (pulseB) {
      errorB -= events;
      digitalWrite(B_STEP_PIN, HIGH);
      positionB += incrementB;
    }
    delayMicroseconds(5);
    digitalWrite(A_STEP_PIN, LOW);
    digitalWrite(B_STEP_PIN, LOW);
    if (!waitUntil(startedAt, intervalUs)) return false;
  }
  return true;
}

bool parseLongStrict(const char *text, long &value) {
  if (!text || !*text || strlen(text) > 6) return false;
  char *end;
  value = strtol(text, &end, 10);
  return *end == '\0';
}

bool parseFloatStrict(const char *text, float &value) {
  if (!text || !*text || strlen(text) > 12) return false;
  char *end;
  value = strtod(text, &end);
  return *end == '\0' && isfinite(value);
}

void setServoPulse(int pulseUs) {
  if (!testServo.attached()) {
    uint8_t channel = testServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US);
    if (channel == INVALID_SERVO) {
      Serial.println(F("ERR: servo attach failed"));
      return;
    }
  }
  servoPulseUs = pulseUs;
  testServo.writeMicroseconds(servoPulseUs);
  lastServoCommandAt = millis();
  Serial.print(F("OK SERVO pulse="));
  Serial.print(servoPulseUs);
  Serial.println(F(" us (auto-off in 10 s)"));
}

void processCommand(char *line) {
  char *tokens[5];
  uint8_t count = 0;
  char *token = strtok(line, " \t");
  while (token && count < 5) {
    tokens[count++] = token;
    token = strtok(NULL, " \t");
  }
  if (!count) return;

  if (count == 1 && !strcmp(tokens[0], "STATUS")) {
    printStatus();
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "HELP")) {
    printHelp();
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "ENABLE")) {
    if (anyLimitHit()) {
      Serial.println(F("ERR: release both limit switches before ENABLE"));
      return;
    }
    digitalWrite(ENABLE_PIN, LOW);
    motorsEnabled = true;
    delay(2);
    Serial.println(F("OK STEPPERS ENABLED"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "DISABLE")) {
    disableMotors();
    Serial.println(F("OK STEPPERS DISABLED; position reference cleared"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "ZERO")) {
    if (!motorsEnabled || anyLimitHit()) {
      Serial.println(F("ERR: enable steppers at a safe point with both limits released"));
      return;
    }
    positionA = 0;
    positionB = 0;
    positionReferenced = true;
    Serial.println(F("OK ZERO (manual test origin; no homing movement occurred)"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "OFF")) {
    servoOff(true);
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "STOP")) {
    stopAll(F("USER STOP"));
    return;
  }

  if (count == 2 && (!strcmp(tokens[0], "SERVO") || !strcmp(tokens[0], "US"))) {
    long value;
    if (!parseLongStrict(tokens[1], value)) {
      Serial.println(F("ERR: use an integer value"));
      return;
    }
    if (!strcmp(tokens[0], "SERVO")) {
      if (value < 0 || value > 180) {
        Serial.println(F("ERR: angle must be 0..180"));
        return;
      }
      int pulseUs = SERVO_MIN_US +
                    (int)(value * (SERVO_MAX_US - SERVO_MIN_US) / 180L);
      setServoPulse(pulseUs);
    } else {
      if (value < SERVO_MIN_US || value > SERVO_MAX_US) {
        Serial.println(F("ERR: pulse must be 500..2500 us"));
        return;
      }
      setServoPulse((int)value);
    }
    return;
  }

  if (count == 4 && !strcmp(tokens[0], "MOTOR")) {
    long steps;
    long rate;
    if (!parseLongStrict(tokens[2], steps) || !parseLongStrict(tokens[3], rate)) {
      Serial.println(F("ERR: use MOTOR A|B signed_steps rate"));
      return;
    }
    uint8_t motorIndex;
    if (!strcmp(tokens[1], "A")) motorIndex = 0;
    else if (!strcmp(tokens[1], "B")) motorIndex = 1;
    else {
      Serial.println(F("ERR: motor must be A or B"));
      return;
    }
    if (rate < 0 || rate > 65535L) {
      Serial.println(F("ERR: invalid step rate"));
      return;
    }
    if (directMotorMove(motorIndex, steps, (unsigned int)rate)) {
      Serial.println(F("OK MOTOR MOVE"));
      printStatus();
    }
    return;
  }

  if (count == 4 && !strcmp(tokens[0], "JOG")) {
    float dx, dy, speed;
    if (!parseFloatStrict(tokens[1], dx) || !parseFloatStrict(tokens[2], dy) ||
        !parseFloatStrict(tokens[3], speed)) {
      Serial.println(F("ERR: use JOG dx_mm dy_mm speed_mm_per_s"));
      return;
    }
    if (jogCoreXY(dx, dy, speed)) {
      Serial.println(F("OK JOG"));
      printStatus();
    }
    return;
  }

  Serial.println(F("ERR: unknown command; type HELP"));
}

void updateLimitInputs(unsigned long now) {
  for (uint8_t i = 0; i < 2; ++i) {
    bool hit = readLimitHit(limitPins[i]);
    if (hit != rawLimit[i]) {
      rawLimit[i] = hit;
      limitChangedAt[i] = now;
    }
    if ((unsigned long)(now - limitChangedAt[i]) >= DEBOUNCE_MS &&
        stableLimit[i] != rawLimit[i]) {
      stableLimit[i] = rawLimit[i];
      printLimit(i);
    }
  }
}

void setup() {
  // Establish safe output levels before changing pin modes.
  digitalWrite(ENABLE_PIN, HIGH);
  pinMode(ENABLE_PIN, OUTPUT);
  digitalWrite(A_STEP_PIN, LOW);
  digitalWrite(B_STEP_PIN, LOW);
  pinMode(A_STEP_PIN, OUTPUT);
  pinMode(B_STEP_PIN, OUTPUT);
  pinMode(A_DIR_PIN, OUTPUT);
  pinMode(B_DIR_PIN, OUTPUT);
  digitalWrite(SERVO_PIN, LOW);
  pinMode(SERVO_PIN, OUTPUT);

  for (uint8_t i = 0; i < 2; ++i) {
    pinMode(limitPins[i], INPUT_PULLUP);
    rawLimit[i] = stableLimit[i] = readLimitHit(limitPins[i]);
    limitChangedAt[i] = millis();
  }

  Serial.begin(115200);
  Serial.println(F("Integrated hardware test ready; all motion outputs are OFF."));
  printHelp();
  printStatus();
}

void loop() {
  unsigned long now = millis();
  updateLimitInputs(now);

  if (motorsEnabled && anyLimitHit()) {
    stopAll(F("LIMIT TRIGGERED"));
  }
  if (testServo.attached() &&
      (unsigned long)(now - lastServoCommandAt) >= SERVO_TIMEOUT_MS) {
    servoOff(true);
  }

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '!') {
      stopAll(F("USER EMERGENCY STOP"));
      discardLine = true;
    } else if (c == '\r' || c == '\n') {
      if (!discardLine && inputLength) {
        inputLine[inputLength] = '\0';
        processCommand(inputLine);
      }
      inputLength = 0;
      discardLine = false;
    } else if (!discardLine) {
      if (inputLength < sizeof(inputLine) - 1) {
        inputLine[inputLength++] = c;
      } else {
        inputLength = 0;
        discardLine = true;
        Serial.println(F("ERR: line too long"));
      }
    }
  }
}
