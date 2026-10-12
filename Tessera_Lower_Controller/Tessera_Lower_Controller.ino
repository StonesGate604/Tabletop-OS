#include <Arduino.h>
#include <Servo.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// ---------------------------------------------------------------------------
// Tessera lower controller for Arduino UNO R3 + CNC Shield V3
// Protocol version: 1
//
// IMPORTANT: Measure the real machine, edit the MACHINE CONFIGURATION section,
// then set MACHINE_CONFIG_CONFIRMED to true. Until then HOME, MOVE_TO and
// SET_TARGET are deliberately rejected.
// ---------------------------------------------------------------------------

// --------------------------- PIN CONFIGURATION ------------------------------
const uint8_t A_STEP_PIN = 2;    // CNC Shield X socket
const uint8_t B_STEP_PIN = 3;    // CNC Shield Y socket
const uint8_t A_DIR_PIN = 5;
const uint8_t B_DIR_PIN = 6;
const uint8_t ENABLE_PIN = 8;    // Active LOW, shared by stepper drivers
const uint8_t X_LIMIT_PIN = 9;
const uint8_t Y_LIMIT_PIN = 10;
const uint8_t SERVO_PIN = 11;

// -------------------------- MACHINE CONFIGURATION ---------------------------
// Confirmed from the successful hardware test and measured machine travel.
const bool MACHINE_CONFIG_CONFIRMED = true;

const bool INVERT_A = false;
const bool INVERT_B = false;
// Physical CoreXY axis signs, confirmed by the current homing-direction test.
// With X_HOME_DIRECTION=-1, X=+1 makes X homing travel toward the left switch.
const int8_t X_AXIS_SIGN = 1;
// With Y_HOME_DIRECTION=-1, Y=+1 makes Y homing travel toward the lower switch.
const int8_t Y_AXIS_SIGN = 1;
// Bare switch: COM -> GND, NO -> signal. Use true for COM + NC.
const bool SWITCH_NORMALLY_CLOSED = false;

// -1 means the switch is at the minimum end; +1 means the maximum end.
const int8_t X_HOME_DIRECTION = -1;
const int8_t Y_HOME_DIRECTION = -1;

// X is the 342 mm width; Y is the 515 mm length.
const float X_TRAVEL_MM = 342.0f;
const float Y_TRAVEL_MM = 515.0f;

// Current hardware: all three M0/M1/M2 (MS1/MS2/MS3) jumpers are installed
// under BOTH A4988 drivers, selecting 1/16 microstep mode.
// 200 full steps/rev * 16 microsteps / (2 mm pitch * 16 teeth) = 100 steps/mm.
const uint8_t MICROSTEPS = 16;
const float MOTOR_FULL_STEPS_PER_REV = 200.0f;
const float BELT_PITCH_MM = 2.0f;
const float PULLEY_TEETH = 16.0f;
const float STEPS_PER_MM = MOTOR_FULL_STEPS_PER_REV * MICROSTEPS /
                           (BELT_PITCH_MM * PULLEY_TEETH);
const float SOFT_LIMIT_MARGIN_MM = 2.0f;

// Normal motion is allowed up to 25 mm/s; homing remains deliberately slower.
const float MAX_MOVE_SPEED_MM_S = 25.0f;
const float TRACKING_SPEED_MM_S = 20.0f;
const float ACCELERATION_MM_S2 = 60.0f;
// Provides enough headroom for 25 mm/s diagonal CoreXY moves at 100 steps/mm.
const float MAX_STEP_EVENT_RATE = 4000.0f;
const float MIN_MOVE_SPEED_MM_S = 0.5f;

const float HOME_FAST_SPEED_MM_S = 8.0f;
const float HOME_SLOW_SPEED_MM_S = 2.0f;
const float HOME_PULL_OFF_MM = 2.0f;
const float HOME_CLEAR_MAX_MM = 20.0f;
// Extra fast-seek allowance for small transmission/travel calibration error.
// This does not enlarge the switch-clear or final pull-off distance.
const float HOME_SEARCH_EXTRA_MM = 80.0f;
const float HOME_RESEEK_MAX_MM = 12.0f;

const int SERVO_MIN_US = 500;
const int SERVO_MAX_US = 2500;
const unsigned long SERVO_TIMEOUT_MS = 10000UL;

const unsigned long TARGET_TIMEOUT_MS = 500UL;
const unsigned long HOST_WATCHDOG_TIMEOUT_MS = 1500UL;
const unsigned long TELEMETRY_INTERVAL_MS = 100UL;
const unsigned long LIMIT_DEBOUNCE_MS = 20UL;

enum Mode : uint8_t {
  MODE_UNREFERENCED,
  MODE_HOMING,
  MODE_IDLE,
  MODE_TASK_MOVE,
  MODE_TRACKING,
  MODE_STOPPING,
  MODE_ERROR
};

enum FaultCode : uint8_t {
  FAULT_NONE,
  FAULT_ESTOP,
  FAULT_LIMIT_X,
  FAULT_LIMIT_Y,
  FAULT_HOME_X_NOT_FOUND,
  FAULT_HOME_Y_NOT_FOUND,
  FAULT_HOME_X_STUCK,
  FAULT_HOME_Y_STUCK
};

enum HomePhase : uint8_t {
  HOME_AXIS_START,
  HOME_CLEAR_SWITCH,
  HOME_FAST_SEEK,
  HOME_FAST_RETRACT,
  HOME_SLOW_SEEK,
  HOME_FINAL_PULL_OFF
};

enum StopReason : uint8_t {
  STOP_NONE,
  STOP_USER,
  STOP_TARGET_TIMEOUT,
  STOP_HOST_TIMEOUT
};

struct MotionPlan {
  bool active;
  bool brakingOnly;
  long targetA;
  long targetB;
  long stepsA;
  long stepsB;
  long totalEvents;
  long eventIndex;
  long errorA;
  long errorB;
  int8_t signA;
  int8_t signB;
  float distancePerEvent;
  float maxSpeed;
  float currentSpeed;
  float directionX;
  float directionY;
  unsigned long nextEventAtUs;
};

Servo actuatorServo;
Mode mode = MODE_UNREFERENCED;
FaultCode faultCode = FAULT_NONE;
MotionPlan plan = {};

bool motorsEnabled = false;
bool positionReferenced = false;
long positionA = 0;
long positionB = 0;

bool rawLimit[2] = {false, false};
bool stableLimit[2] = {false, false};
unsigned long limitChangedAt[2] = {0, 0};

uint8_t homeAxis = 0;  // 0 = X, 1 = Y
HomePhase homePhase = HOME_AXIS_START;
long homePhaseEvents = 0;
long homeReleaseEvent = 0;
bool homeReleaseSeen = false;
unsigned long homeNextEventAtUs = 0;

unsigned long activeId = 0;
unsigned long lastTaskId = 0;
unsigned long lastTargetSequence = 0;
unsigned long lastTargetAtMs = 0;
unsigned long telemetrySequence = 0;
bool telemetryEnabled = false;
bool hostWatchdogEnabled = false;
bool hostWatchdogTripped = false;
unsigned long lastHostCommandAtMs = 0;
float commandedTargetX = 0.0f;
float commandedTargetY = 0.0f;
bool trackingDirectionChange = false;
StopReason stopReason = STOP_NONE;
unsigned long cancelledTaskId = 0;

int servoAngle = 90;
unsigned long lastServoCommandAtMs = 0;

char inputLine[96];
uint8_t inputLength = 0;
bool discardInputLine = false;

bool timeReached(unsigned long now, unsigned long target) {
  return (long)(now - target) >= 0;
}

bool isNewer32(unsigned long candidate, unsigned long previous) {
  if (previous == 0) return true;
  return (int32_t)((uint32_t)candidate - (uint32_t)previous) > 0;
}

bool readLimitRaw(uint8_t axis) {
  uint8_t pin = axis == 0 ? X_LIMIT_PIN : Y_LIMIT_PIN;
  return digitalRead(pin) == (SWITCH_NORMALLY_CLOSED ? HIGH : LOW);
}

float xFromMotorSteps(long motorA, long motorB) {
  return X_AXIS_SIGN * (motorA + motorB) / (2.0f * STEPS_PER_MM);
}

float yFromMotorSteps(long motorA, long motorB) {
  return Y_AXIS_SIGN * (motorA - motorB) / (2.0f * STEPS_PER_MM);
}

float xPosition() {
  return xFromMotorSteps(positionA, positionB);
}

float yPosition() {
  return yFromMotorSteps(positionA, positionB);
}

float xSoftMinimum() { return SOFT_LIMIT_MARGIN_MM; }
float ySoftMinimum() { return SOFT_LIMIT_MARGIN_MM; }
float xSoftMaximum() { return X_TRAVEL_MM - SOFT_LIMIT_MARGIN_MM; }
float ySoftMaximum() { return Y_TRAVEL_MM - SOFT_LIMIT_MARGIN_MM; }

bool validMachineConfiguration() {
  return MACHINE_CONFIG_CONFIRMED &&
         (X_HOME_DIRECTION == -1 || X_HOME_DIRECTION == 1) &&
         (Y_HOME_DIRECTION == -1 || Y_HOME_DIRECTION == 1) &&
         (X_AXIS_SIGN == -1 || X_AXIS_SIGN == 1) &&
         (Y_AXIS_SIGN == -1 || Y_AXIS_SIGN == 1) &&
         STEPS_PER_MM > 0.0f && X_TRAVEL_MM > 2.0f * SOFT_LIMIT_MARGIN_MM &&
         Y_TRAVEL_MM > 2.0f * SOFT_LIMIT_MARGIN_MM &&
         HOME_PULL_OFF_MM >= SOFT_LIMIT_MARGIN_MM;
}

const __FlashStringHelper *modeName(Mode value) {
  switch (value) {
    case MODE_UNREFERENCED: return F("UNREFERENCED");
    case MODE_HOMING: return F("HOMING");
    case MODE_IDLE: return F("IDLE");
    case MODE_TASK_MOVE: return F("TASK_MOVE");
    case MODE_TRACKING: return F("TRACKING");
    case MODE_STOPPING: return F("STOPPING");
    default: return F("ERROR");
  }
}

const __FlashStringHelper *faultName(FaultCode value) {
  switch (value) {
    case FAULT_ESTOP: return F("ESTOP");
    case FAULT_LIMIT_X: return F("LIMIT_X");
    case FAULT_LIMIT_Y: return F("LIMIT_Y");
    case FAULT_HOME_X_NOT_FOUND: return F("HOME_X_NOT_FOUND");
    case FAULT_HOME_Y_NOT_FOUND: return F("HOME_Y_NOT_FOUND");
    case FAULT_HOME_X_STUCK: return F("HOME_X_STUCK");
    case FAULT_HOME_Y_STUCK: return F("HOME_Y_STUCK");
    default: return F("NONE");
  }
}

void enableMotors() {
  digitalWrite(ENABLE_PIN, LOW);
  motorsEnabled = true;
}

void disableMotors() {
  digitalWrite(ENABLE_PIN, HIGH);
  motorsEnabled = false;
}

void servoOff() {
  if (actuatorServo.attached()) actuatorServo.detach();
  digitalWrite(SERVO_PIN, LOW);
}

void clearPlanner() {
  plan.active = false;
  plan.currentSpeed = 0.0f;
  trackingDirectionChange = false;
}

void printError(const __FlashStringHelper *code, unsigned long relatedId) {
  Serial.print(F("ERROR "));
  Serial.print(code);
  Serial.print(' ');
  Serial.println(relatedId);
}

void enterFault(FaultCode code, unsigned long relatedId) {
  clearPlanner();
  disableMotors();
  servoOff();
  positionReferenced = false;
  mode = MODE_ERROR;
  faultCode = code;
  activeId = 0;
  printError(faultName(code), relatedId);
}

void emergencyStop() {
  enterFault(FAULT_ESTOP, activeId);
  inputLength = 0;
  discardInputLine = true;
}

void updateLimitDebounce(unsigned long nowMs) {
  for (uint8_t axis = 0; axis < 2; ++axis) {
    bool hit = readLimitRaw(axis);
    if (hit != rawLimit[axis]) {
      rawLimit[axis] = hit;
      limitChangedAt[axis] = nowMs;
    }
    if ((unsigned long)(nowMs - limitChangedAt[axis]) >= LIMIT_DEBOUNCE_MS &&
        stableLimit[axis] != rawLimit[axis]) {
      stableLimit[axis] = rawLimit[axis];
    }
  }
}

void setDirectionPins(int8_t signA, int8_t signB) {
  digitalWrite(A_DIR_PIN, (signA > 0) ^ INVERT_A);
  digitalWrite(B_DIR_PIN, (signB > 0) ^ INVERT_B);
}

void emitStepEvent(bool stepA, bool stepB, int8_t signA, int8_t signB) {
  if (stepA) digitalWrite(A_STEP_PIN, HIGH);
  if (stepB) digitalWrite(B_STEP_PIN, HIGH);
  delayMicroseconds(4);
  if (stepA) {
    digitalWrite(A_STEP_PIN, LOW);
    positionA += signA;
  }
  if (stepB) {
    digitalWrite(B_STEP_PIN, LOW);
    positionB += signB;
  }
}

bool targetInsideSoftLimits(float x, float y) {
  return isfinite(x) && isfinite(y) &&
         x >= xSoftMinimum() && x <= xSoftMaximum() &&
         y >= ySoftMinimum() && y <= ySoftMaximum();
}

unsigned long eventIntervalUs(float distancePerEvent, float speed) {
  if (speed <= 0.0f) return 1000000UL;
  float interval = 1000000.0f * distancePerEvent / speed;
  if (interval < 50.0f) interval = 50.0f;
  if (interval > 1000000.0f) interval = 1000000.0f;
  return (unsigned long)interval;
}

bool startMotionPlan(float targetX, float targetY, float requestedSpeed,
                     float initialSpeed, bool brakingOnly) {
  long targetXSteps = lroundf(targetX * STEPS_PER_MM);
  long targetYSteps = lroundf(targetY * STEPS_PER_MM);
  long targetA = X_AXIS_SIGN * targetXSteps + Y_AXIS_SIGN * targetYSteps;
  long targetB = X_AXIS_SIGN * targetXSteps - Y_AXIS_SIGN * targetYSteps;
  long deltaA = targetA - positionA;
  long deltaB = targetB - positionB;
  long stepsA = labs(deltaA);
  long stepsB = labs(deltaB);
  long events = stepsA > stepsB ? stepsA : stepsB;
  if (events == 0) {
    clearPlanner();
    return false;
  }

  float quantizedTargetX = xFromMotorSteps(targetA, targetB);
  float quantizedTargetY = yFromMotorSteps(targetA, targetB);
  float dx = quantizedTargetX - xPosition();
  float dy = quantizedTargetY - yPosition();
  float distance = sqrtf(dx * dx + dy * dy);
  if (distance <= 0.0f) {
    clearPlanner();
    return false;
  }

  plan.active = true;
  plan.brakingOnly = brakingOnly;
  plan.targetA = targetA;
  plan.targetB = targetB;
  plan.stepsA = stepsA;
  plan.stepsB = stepsB;
  plan.totalEvents = events;
  plan.eventIndex = 0;
  plan.errorA = events / 2;
  plan.errorB = events / 2;
  plan.signA = deltaA >= 0 ? 1 : -1;
  plan.signB = deltaB >= 0 ? 1 : -1;
  plan.distancePerEvent = distance / events;
  plan.directionX = dx / distance;
  plan.directionY = dy / distance;
  plan.maxSpeed = fminf(requestedSpeed,
                        MAX_STEP_EVENT_RATE * plan.distancePerEvent);

  float startSpeed = sqrtf(2.0f * ACCELERATION_MM_S2 * plan.distancePerEvent);
  startSpeed = fmaxf(MIN_MOVE_SPEED_MM_S, startSpeed);
  startSpeed = fminf(startSpeed, plan.maxSpeed);
  if (initialSpeed > startSpeed) startSpeed = fminf(initialSpeed, plan.maxSpeed);
  plan.currentSpeed = startSpeed;

  setDirectionPins(plan.signA, plan.signB);
  plan.nextEventAtUs = micros() + eventIntervalUs(plan.distancePerEvent,
                                                   plan.currentSpeed);
  return true;
}

void finishMotionPlan();

void serviceMotionPlan() {
  if (!plan.active) return;

  if (readLimitRaw(0)) {
    enterFault(FAULT_LIMIT_X, activeId);
    return;
  }
  if (readLimitRaw(1)) {
    enterFault(FAULT_LIMIT_Y, activeId);
    return;
  }

  unsigned long nowUs = micros();
  if (!timeReached(nowUs, plan.nextEventAtUs)) return;

  float remainingDistance =
      (plan.totalEvents - plan.eventIndex) * plan.distancePerEvent;
  float brakingSpeed = sqrtf(2.0f * ACCELERATION_MM_S2 * remainingDistance);
  float desiredSpeed = fminf(plan.maxSpeed, brakingSpeed);
  desiredSpeed = fminf(desiredSpeed,
                       MAX_STEP_EVENT_RATE * plan.distancePerEvent);

  float speedSquared = plan.currentSpeed * plan.currentSpeed;
  float deltaSpeedSquared = 2.0f * ACCELERATION_MM_S2 * plan.distancePerEvent;
  if (plan.currentSpeed < desiredSpeed) {
    plan.currentSpeed = fminf(desiredSpeed, sqrtf(speedSquared + deltaSpeedSquared));
  } else if (plan.currentSpeed > desiredSpeed) {
    float reduced = speedSquared - deltaSpeedSquared;
    plan.currentSpeed = fmaxf(desiredSpeed,
                              sqrtf(reduced > 0.0f ? reduced : 0.0f));
  }
  if (plan.currentSpeed < MIN_MOVE_SPEED_MM_S) {
    plan.currentSpeed = MIN_MOVE_SPEED_MM_S;
  }

  plan.errorA += plan.stepsA;
  plan.errorB += plan.stepsB;
  bool pulseA = plan.errorA >= plan.totalEvents;
  bool pulseB = plan.errorB >= plan.totalEvents;
  if (pulseA) plan.errorA -= plan.totalEvents;
  if (pulseB) plan.errorB -= plan.totalEvents;
  emitStepEvent(pulseA, pulseB, plan.signA, plan.signB);
  ++plan.eventIndex;

  if (plan.eventIndex >= plan.totalEvents) {
    positionA = plan.targetA;
    positionB = plan.targetB;
    plan.active = false;
    plan.currentSpeed = 0.0f;
    finishMotionPlan();
    return;
  }

  plan.nextEventAtUs = micros() + eventIntervalUs(plan.distancePerEvent,
                                                   plan.currentSpeed);
}

void startBrakePlan(bool forTrackingDirectionChange) {
  float speed = plan.currentSpeed;
  float directionX = plan.directionX;
  float directionY = plan.directionY;
  if (!plan.active || speed <= MIN_MOVE_SPEED_MM_S) {
    clearPlanner();
    if (forTrackingDirectionChange) {
      startMotionPlan(commandedTargetX, commandedTargetY,
                      TRACKING_SPEED_MM_S, 0.0f, false);
    } else if (mode == MODE_STOPPING) {
      finishMotionPlan();
    }
    return;
  }

  float brakingDistance = speed * speed / (2.0f * ACCELERATION_MM_S2);
  brakingDistance += plan.distancePerEvent;
  float brakeX = xPosition() + directionX * brakingDistance;
  float brakeY = yPosition() + directionY * brakingDistance;
  brakeX = constrain(brakeX, xSoftMinimum(), xSoftMaximum());
  brakeY = constrain(brakeY, ySoftMinimum(), ySoftMaximum());
  trackingDirectionChange = forTrackingDirectionChange;
  if (!startMotionPlan(brakeX, brakeY, speed, speed, true)) {
    if (forTrackingDirectionChange) {
      startMotionPlan(commandedTargetX, commandedTargetY,
                      TRACKING_SPEED_MM_S, 0.0f, false);
    } else if (mode == MODE_STOPPING) {
      finishMotionPlan();
    }
  }
}

void finishMotionPlan() {
  if (mode == MODE_TASK_MOVE) {
    unsigned long completedId = activeId;
    activeId = 0;
    mode = MODE_IDLE;
    Serial.print(F("COMPLETED "));
    Serial.println(completedId);
    return;
  }

  if (mode == MODE_TRACKING) {
    if (trackingDirectionChange) {
      trackingDirectionChange = false;
      startMotionPlan(commandedTargetX, commandedTargetY,
                      TRACKING_SPEED_MM_S, 0.0f, false);
    }
    return;
  }

  if (mode == MODE_STOPPING) {
    mode = positionReferenced ? MODE_IDLE : MODE_UNREFERENCED;
    activeId = 0;
    commandedTargetX = xPosition();
    commandedTargetY = yPosition();
    if (cancelledTaskId != 0) {
      Serial.print(F("CANCELLED "));
      Serial.println(cancelledTaskId);
      cancelledTaskId = 0;
    }
    Serial.print(F("STOPPED "));
    if (stopReason == STOP_TARGET_TIMEOUT) {
      Serial.println(F("TARGET_TIMEOUT"));
    } else if (stopReason == STOP_HOST_TIMEOUT) {
      Serial.println(F("HOST_TIMEOUT"));
    } else {
      Serial.println(F("USER"));
    }
    stopReason = STOP_NONE;
  }
}

void requestControlledStop(StopReason reason) {
  if (mode == MODE_HOMING) {
    clearPlanner();
    disableMotors();
    positionReferenced = false;
    mode = MODE_UNREFERENCED;
    activeId = 0;
    Serial.println(reason == STOP_HOST_TIMEOUT ? F("STOPPED HOST_TIMEOUT")
                                               : F("STOPPED HOME_ABORTED"));
    return;
  }

  if (mode != MODE_TASK_MOVE && mode != MODE_TRACKING && mode != MODE_STOPPING) {
    Serial.println(F("STOPPED ALREADY_STATIONARY"));
    return;
  }

  if (mode == MODE_TASK_MOVE) cancelledTaskId = activeId;
  stopReason = reason;
  mode = MODE_STOPPING;
  if (plan.active) {
    startBrakePlan(false);
  } else {
    finishMotionPlan();
  }
}

void setAxisCoordinate(uint8_t axis, float coordinateMm) {
  long coordinateSteps = lroundf(coordinateMm * STEPS_PER_MM);
  long xSteps = X_AXIS_SIGN * (positionA + positionB) / 2;
  long ySteps = Y_AXIS_SIGN * (positionA - positionB) / 2;
  if (axis == 0) xSteps = coordinateSteps;
  else ySteps = coordinateSteps;
  positionA = X_AXIS_SIGN * xSteps + Y_AXIS_SIGN * ySteps;
  positionB = X_AXIS_SIGN * xSteps - Y_AXIS_SIGN * ySteps;
}

int8_t homeDirection(uint8_t axis) {
  return axis == 0 ? X_HOME_DIRECTION : Y_HOME_DIRECTION;
}

float homeTravel(uint8_t axis) {
  return axis == 0 ? X_TRAVEL_MM : Y_TRAVEL_MM;
}

long mmToEvents(float mm) {
  return lroundf(mm * STEPS_PER_MM);
}

void setHomePhase(HomePhase phase) {
  homePhase = phase;
  homePhaseEvents = 0;
  homeReleaseEvent = 0;
  homeReleaseSeen = false;
  homeNextEventAtUs = micros();
}

void failHoming(bool notFound) {
  if (homeAxis == 0) {
    enterFault(notFound ? FAULT_HOME_X_NOT_FOUND : FAULT_HOME_X_STUCK, 0);
  } else {
    enterFault(notFound ? FAULT_HOME_Y_NOT_FOUND : FAULT_HOME_Y_STUCK, 0);
  }
}

void beginHomeAxis(uint8_t axis) {
  homeAxis = axis;
  setHomePhase(HOME_AXIS_START);
}

bool emitHomeStep(int8_t axisDirection, float speedMmS) {
  unsigned long nowUs = micros();
  if (!timeReached(nowUs, homeNextEventAtUs)) return false;

  int8_t signA;
  int8_t signB;
  if (homeAxis == 0) {
    signA = axisDirection * X_AXIS_SIGN;
    signB = axisDirection * X_AXIS_SIGN;
  } else {
    signA = axisDirection * Y_AXIS_SIGN;
    signB = -axisDirection * Y_AXIS_SIGN;
  }
  setDirectionPins(signA, signB);
  emitStepEvent(true, true, signA, signB);
  ++homePhaseEvents;
  unsigned long interval = (unsigned long)(1000000.0f /
      fminf(MAX_STEP_EVENT_RATE, speedMmS * STEPS_PER_MM));
  homeNextEventAtUs = micros() + interval;
  return true;
}

bool releasedAndPulledOff(bool switchHit) {
  if (switchHit) {
    homeReleaseSeen = false;
    return false;
  }
  if (!homeReleaseSeen) {
    homeReleaseSeen = true;
    homeReleaseEvent = homePhaseEvents;
  }
  return homePhaseEvents - homeReleaseEvent >= mmToEvents(HOME_PULL_OFF_MM);
}

void completeHomeAxis() {
  if (homeAxis == 0) {
    beginHomeAxis(1);
    return;
  }
  positionReferenced = true;
  mode = MODE_IDLE;
  activeId = 0;
  commandedTargetX = xPosition();
  commandedTargetY = yPosition();
  Serial.println(F("COMPLETED HOME"));
}

void serviceHoming() {
  if (mode != MODE_HOMING) return;

  bool switchHit = stableLimit[homeAxis];
  int8_t towardSwitch = homeDirection(homeAxis);
  int8_t awayFromSwitch = -towardSwitch;

  switch (homePhase) {
    case HOME_AXIS_START:
      setHomePhase(switchHit ? HOME_CLEAR_SWITCH : HOME_FAST_SEEK);
      return;

    case HOME_CLEAR_SWITCH:
      if (releasedAndPulledOff(switchHit)) {
        setHomePhase(HOME_FAST_SEEK);
        return;
      }
      if (homePhaseEvents > mmToEvents(HOME_CLEAR_MAX_MM)) {
        failHoming(false);
        return;
      }
      emitHomeStep(awayFromSwitch, HOME_SLOW_SPEED_MM_S);
      return;

    case HOME_FAST_SEEK:
      if (switchHit) {
        setHomePhase(HOME_FAST_RETRACT);
        return;
      }
      if (homePhaseEvents > mmToEvents(homeTravel(homeAxis) + HOME_SEARCH_EXTRA_MM)) {
        failHoming(true);
        return;
      }
      emitHomeStep(towardSwitch, HOME_FAST_SPEED_MM_S);
      return;

    case HOME_FAST_RETRACT:
      if (releasedAndPulledOff(switchHit)) {
        setHomePhase(HOME_SLOW_SEEK);
        return;
      }
      if (homePhaseEvents > mmToEvents(HOME_CLEAR_MAX_MM)) {
        failHoming(false);
        return;
      }
      emitHomeStep(awayFromSwitch, HOME_SLOW_SPEED_MM_S);
      return;

    case HOME_SLOW_SEEK:
      if (switchHit) {
        float coordinate = towardSwitch < 0 ? 0.0f : homeTravel(homeAxis);
        setAxisCoordinate(homeAxis, coordinate);
        setHomePhase(HOME_FINAL_PULL_OFF);
        return;
      }
      if (homePhaseEvents > mmToEvents(HOME_RESEEK_MAX_MM)) {
        failHoming(true);
        return;
      }
      emitHomeStep(towardSwitch, HOME_SLOW_SPEED_MM_S);
      return;

    case HOME_FINAL_PULL_OFF:
      if (releasedAndPulledOff(switchHit)) {
        completeHomeAxis();
        return;
      }
      if (homePhaseEvents > mmToEvents(HOME_CLEAR_MAX_MM)) {
        failHoming(false);
        return;
      }
      emitHomeStep(awayFromSwitch, HOME_SLOW_SPEED_MM_S);
      return;
  }
}

void startHoming() {
  if (!validMachineConfiguration()) {
    printError(F("CONFIG_NOT_CONFIRMED"), 0);
    return;
  }
  if (mode == MODE_TASK_MOVE || mode == MODE_TRACKING ||
      mode == MODE_STOPPING || mode == MODE_HOMING) {
    printError(F("BUSY"), activeId);
    return;
  }
  if (mode == MODE_ERROR) {
    printError(F("CLEAR_ERROR_REQUIRED"), 0);
    return;
  }

  clearPlanner();
  servoOff();
  enableMotors();
  positionReferenced = false;
  faultCode = FAULT_NONE;
  mode = MODE_HOMING;
  activeId = 0;
  Serial.println(F("ACK HOME"));
  Serial.println(F("STARTED HOME"));
  beginHomeAxis(0);
}

bool parseUnsignedLongStrict(const char *text, unsigned long &value) {
  if (!text || !*text || strlen(text) > 10) return false;
  for (const char *p = text; *p; ++p) {
    if (*p < '0' || *p > '9') return false;
  }
  char *end;
  value = strtoul(text, &end, 10);
  return *end == '\0';
}

bool parseFloatStrict(const char *text, float &value) {
  if (!text || !*text || strlen(text) > 14) return false;
  char *end;
  value = strtod(text, &end);
  return *end == '\0' && isfinite(value);
}

void printState() {
  ++telemetrySequence;
  Serial.print(F("STATE "));
  Serial.print(telemetrySequence);
  Serial.print(' ');
  Serial.print(modeName(mode));
  Serial.print(' ');
  Serial.print(activeId);
  Serial.print(' ');
  Serial.print(xPosition(), 3);
  Serial.print(' ');
  Serial.print(yPosition(), 3);
  Serial.print(' ');
  Serial.print(commandedTargetX, 3);
  Serial.print(' ');
  Serial.println(commandedTargetY, 3);
}

void printIoState() {
  Serial.print(F("IO X_LIMIT="));
  Serial.print(stableLimit[0] ? 1 : 0);
  Serial.print(F(" Y_LIMIT="));
  Serial.print(stableLimit[1] ? 1 : 0);
  Serial.print(F(" MOTORS="));
  Serial.print(motorsEnabled ? 1 : 0);
  Serial.print(F(" WATCHDOG="));
  Serial.print(hostWatchdogEnabled ? F("ON") : F("OFF"));
  Serial.print(F(" REFERENCED="));
  Serial.print(positionReferenced ? 1 : 0);
  Serial.print(F(" SERVO="));
  if (actuatorServo.attached()) Serial.print(servoAngle);
  else Serial.print(F("OFF"));
  Serial.print(F(" FAULT="));
  Serial.println(faultName(faultCode));
}

void printConfiguration() {
  Serial.print(F("CONFIG CONFIRMED="));
  Serial.print(MACHINE_CONFIG_CONFIRMED ? 1 : 0);
  Serial.print(F(" MICROSTEPS="));
  Serial.print(MICROSTEPS);
  Serial.print(F(" STEPS_PER_MM="));
  Serial.print(STEPS_PER_MM, 3);
  Serial.print(F(" X_TRAVEL="));
  Serial.print(X_TRAVEL_MM, 2);
  Serial.print(F(" Y_TRAVEL="));
  Serial.print(Y_TRAVEL_MM, 2);
  Serial.print(F(" X_HOME_DIR="));
  Serial.print(X_HOME_DIRECTION);
  Serial.print(F(" Y_HOME_DIR="));
  Serial.print(Y_HOME_DIRECTION);
  Serial.print(F(" X_AXIS_SIGN="));
  Serial.print(X_AXIS_SIGN);
  Serial.print(F(" Y_AXIS_SIGN="));
  Serial.println(Y_AXIS_SIGN);
}

void setServoAngle(long angle) {
  if (angle < 0 || angle > 180) {
    printError(F("SERVO_RANGE"), 0);
    return;
  }
  if (mode == MODE_ERROR) {
    printError(F("IN_ERROR"), 0);
    return;
  }
  if (!actuatorServo.attached()) {
    uint8_t channel = actuatorServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US);
    if (channel == INVALID_SERVO) {
      printError(F("SERVO_ATTACH"), 0);
      return;
    }
  }
  servoAngle = (int)angle;
  actuatorServo.write(servoAngle);
  lastServoCommandAtMs = millis();
  Serial.print(F("OK SERVO "));
  Serial.println(servoAngle);
}

void startTaskMove(unsigned long commandId, float x, float y, float speed) {
  if (!validMachineConfiguration()) {
    printError(F("CONFIG_NOT_CONFIRMED"), commandId);
    return;
  }
  if (!positionReferenced || mode == MODE_UNREFERENCED) {
    printError(F("HOME_REQUIRED"), commandId);
    return;
  }
  if (mode != MODE_IDLE) {
    if (mode == MODE_TASK_MOVE && commandId == activeId) {
      Serial.print(F("ACK "));
      Serial.println(commandId);
    } else {
      printError(F("BUSY"), commandId);
    }
    return;
  }
  if (commandId == 0 || !isNewer32(commandId, lastTaskId)) {
    printError(F("STALE_ID"), commandId);
    return;
  }
  if (!targetInsideSoftLimits(x, y) || !isfinite(speed) ||
      speed < MIN_MOVE_SPEED_MM_S || speed > MAX_MOVE_SPEED_MM_S) {
    printError(F("RANGE"), commandId);
    return;
  }

  lastTaskId = commandId;
  activeId = commandId;
  commandedTargetX = x;
  commandedTargetY = y;
  mode = MODE_TASK_MOVE;
  Serial.print(F("ACK "));
  Serial.println(commandId);
  Serial.print(F("STARTED "));
  Serial.println(commandId);
  if (!startMotionPlan(x, y, speed, 0.0f, false)) finishMotionPlan();
}

void updateTrackingTarget(unsigned long sequence, float x, float y) {
  if (!validMachineConfiguration()) {
    printError(F("CONFIG_NOT_CONFIRMED"), sequence);
    return;
  }
  if (!positionReferenced || mode == MODE_UNREFERENCED) {
    printError(F("HOME_REQUIRED"), sequence);
    return;
  }
  if (mode != MODE_IDLE && mode != MODE_TRACKING) {
    printError(F("BUSY"), sequence);
    return;
  }
  if (sequence == 0 || !isNewer32(sequence, lastTargetSequence)) {
    Serial.print(F("IGNORED_TARGET "));
    Serial.println(sequence);
    return;
  }
  if (!targetInsideSoftLimits(x, y)) {
    printError(F("RANGE"), sequence);
    return;
  }

  lastTargetSequence = sequence;
  lastTargetAtMs = millis();
  activeId = sequence;
  commandedTargetX = x;
  commandedTargetY = y;
  Serial.print(F("ACK_TARGET "));
  Serial.println(sequence);

  if (mode == MODE_IDLE) {
    mode = MODE_TRACKING;
    startMotionPlan(x, y, TRACKING_SPEED_MM_S, 0.0f, false);
    return;
  }

  if (trackingDirectionChange) return;
  if (!plan.active) {
    startMotionPlan(x, y, TRACKING_SPEED_MM_S, 0.0f, false);
    return;
  }

  float dx = x - xPosition();
  float dy = y - yPosition();
  float distance = sqrtf(dx * dx + dy * dy);
  if (distance <= 0.0f) {
    clearPlanner();
    return;
  }
  float newDirectionX = dx / distance;
  float newDirectionY = dy / distance;
  float directionDot = plan.directionX * newDirectionX +
                       plan.directionY * newDirectionY;
  float retainedSpeed = plan.currentSpeed;
  if (directionDot < 0.5f && retainedSpeed > 2.0f) {
    startBrakePlan(true);
  } else {
    startMotionPlan(x, y, TRACKING_SPEED_MM_S, retainedSpeed, false);
  }
}

void processCommand(char *line) {
  char *tokens[7];
  uint8_t count = 0;
  char *token = strtok(line, " \t");
  while (token && count < 7) {
    tokens[count++] = token;
    token = strtok(NULL, " \t");
  }
  if (!count) return;

  if (count == 1 && !strcmp(tokens[0], "PING")) {
    Serial.println(F("PONG 1"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "IDENTIFY")) {
    Serial.println(F("READY TESSERA_LOWER 1"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "STATUS")) {
    printState();
    printIoState();
    return;
  }
  if (count == 2 && !strcmp(tokens[0], "TELEMETRY")) {
    if (!strcmp(tokens[1], "ON")) {
      telemetryEnabled = true;
      Serial.println(F("OK TELEMETRY ON"));
      printState();
    } else if (!strcmp(tokens[1], "OFF")) {
      telemetryEnabled = false;
      Serial.println(F("OK TELEMETRY OFF"));
    } else {
      printError(F("USE_TELEMETRY_ON_OR_OFF"), 0);
    }
    return;
  }
  if (count == 2 && !strcmp(tokens[0], "WATCHDOG")) {
    if (!strcmp(tokens[1], "ON")) {
      hostWatchdogEnabled = true;
      hostWatchdogTripped = false;
      lastHostCommandAtMs = millis();
      Serial.println(F("OK WATCHDOG ON"));
    } else if (!strcmp(tokens[1], "OFF")) {
      hostWatchdogEnabled = false;
      hostWatchdogTripped = false;
      Serial.println(F("OK WATCHDOG OFF"));
    } else {
      printError(F("USE_WATCHDOG_ON_OR_OFF"), 0);
    }
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "CONFIG")) {
    printConfiguration();
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "HOME")) {
    startHoming();
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "ENABLE")) {
    if (mode == MODE_ERROR) {
      printError(F("CLEAR_ERROR_REQUIRED"), 0);
    } else if (mode == MODE_HOMING || mode == MODE_TASK_MOVE ||
               mode == MODE_TRACKING || mode == MODE_STOPPING) {
      printError(F("BUSY"), activeId);
    } else {
      enableMotors();
      Serial.println(F("OK ENABLED; HOME REQUIRED IF UNREFERENCED"));
    }
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "STOP")) {
    requestControlledStop(STOP_USER);
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "DISABLE")) {
    clearPlanner();
    disableMotors();
    servoOff();
    positionReferenced = false;
    mode = MODE_UNREFERENCED;
    faultCode = FAULT_NONE;
    activeId = 0;
    Serial.println(F("OK DISABLED"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "CLEAR_ERROR")) {
    if (mode != MODE_ERROR) {
      Serial.println(F("OK NO_ERROR"));
      return;
    }
    faultCode = FAULT_NONE;
    mode = MODE_UNREFERENCED;
    activeId = 0;
    Serial.println(F("OK ERROR_CLEARED; HOME REQUIRED"));
    return;
  }
  if (count == 1 && !strcmp(tokens[0], "SERVO_OFF")) {
    servoOff();
    Serial.println(F("OK SERVO_OFF"));
    return;
  }
  if (count == 2 && !strcmp(tokens[0], "SERVO")) {
    unsigned long angle;
    if (!parseUnsignedLongStrict(tokens[1], angle)) {
      printError(F("PARSE"), 0);
      return;
    }
    setServoAngle((long)angle);
    return;
  }
  if (count == 5 && !strcmp(tokens[0], "MOVE_TO")) {
    unsigned long commandId;
    float x, y, speed;
    if (!parseUnsignedLongStrict(tokens[1], commandId) ||
        !parseFloatStrict(tokens[2], x) ||
        !parseFloatStrict(tokens[3], y) ||
        !parseFloatStrict(tokens[4], speed)) {
      printError(F("PARSE"), 0);
      return;
    }
    startTaskMove(commandId, x, y, speed);
    return;
  }
  if (count == 4 && !strcmp(tokens[0], "SET_TARGET")) {
    unsigned long sequence;
    float x, y;
    if (!parseUnsignedLongStrict(tokens[1], sequence) ||
        !parseFloatStrict(tokens[2], x) ||
        !parseFloatStrict(tokens[3], y)) {
      printError(F("PARSE"), 0);
      return;
    }
    updateTrackingTarget(sequence, x, y);
    return;
  }

  printError(F("UNKNOWN_COMMAND"), 0);
}

void serviceSerialInput() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '!') {
      emergencyStop();
    } else if (c == '\r' || c == '\n') {
      if (!discardInputLine && inputLength) {
        inputLine[inputLength] = '\0';
        lastHostCommandAtMs = millis();
        hostWatchdogTripped = false;
        processCommand(inputLine);
      }
      inputLength = 0;
      discardInputLine = false;
    } else if (!discardInputLine) {
      if (inputLength < sizeof(inputLine) - 1) {
        inputLine[inputLength++] = c;
      } else {
        inputLength = 0;
        discardInputLine = true;
        printError(F("LINE_TOO_LONG"), 0);
      }
    }
  }
}

void serviceTargetTimeout(unsigned long nowMs) {
  if (mode != MODE_TRACKING) return;
  if ((unsigned long)(nowMs - lastTargetAtMs) < TARGET_TIMEOUT_MS) return;
  printError(F("TARGET_TIMEOUT"), lastTargetSequence);
  requestControlledStop(STOP_TARGET_TIMEOUT);
}

void serviceHostWatchdog(unsigned long nowMs) {
  if (!hostWatchdogEnabled || hostWatchdogTripped) return;
  if (mode != MODE_HOMING && mode != MODE_TASK_MOVE &&
      mode != MODE_TRACKING && mode != MODE_STOPPING) return;
  if ((unsigned long)(nowMs - lastHostCommandAtMs) <
      HOST_WATCHDOG_TIMEOUT_MS) return;

  hostWatchdogTripped = true;
  printError(F("HOST_TIMEOUT"), activeId);
  if (mode == MODE_STOPPING) {
    stopReason = STOP_HOST_TIMEOUT;
    if (!plan.active) finishMotionPlan();
    return;
  }
  requestControlledStop(STOP_HOST_TIMEOUT);
}

void serviceServoTimeout(unsigned long nowMs) {
  if (!actuatorServo.attached()) return;
  if ((unsigned long)(nowMs - lastServoCommandAtMs) >= SERVO_TIMEOUT_MS) {
    servoOff();
    Serial.println(F("EVENT SERVO_TIMEOUT"));
  }
}

void serviceUnexpectedLimits() {
  if (mode == MODE_HOMING || mode == MODE_ERROR || !motorsEnabled) return;
  if (readLimitRaw(0)) enterFault(FAULT_LIMIT_X, activeId);
  else if (readLimitRaw(1)) enterFault(FAULT_LIMIT_Y, activeId);
}

void serviceTelemetry(unsigned long nowMs) {
  static unsigned long lastTelemetryAtMs = 0;
  if (!telemetryEnabled) return;
  if ((unsigned long)(nowMs - lastTelemetryAtMs) < TELEMETRY_INTERVAL_MS) return;
  // Avoid blocking the motion loop if the host is not draining serial output.
  if (Serial.availableForWrite() < 32) return;
  lastTelemetryAtMs = nowMs;
  printState();
}

void setup() {
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
  pinMode(X_LIMIT_PIN, INPUT_PULLUP);
  pinMode(Y_LIMIT_PIN, INPUT_PULLUP);

  unsigned long nowMs = millis();
  for (uint8_t axis = 0; axis < 2; ++axis) {
    rawLimit[axis] = stableLimit[axis] = readLimitRaw(axis);
    limitChangedAt[axis] = nowMs;
  }

  Serial.begin(115200);
  Serial.println(F("READY TESSERA_LOWER 1"));
  printConfiguration();
  if (!validMachineConfiguration()) {
    Serial.println(F("LOCKED: confirm machine configuration before automatic motion"));
  }
  printIoState();
}

void loop() {
  unsigned long nowMs = millis();
  updateLimitDebounce(nowMs);

  // Motion service stays before verbose serial/telemetry work.
  serviceHoming();
  serviceMotionPlan();
  serviceSerialInput();
  serviceUnexpectedLimits();
  serviceHostWatchdog(nowMs);
  serviceTargetTimeout(nowMs);
  serviceServoTimeout(nowMs);
  serviceTelemetry(nowMs);
}

