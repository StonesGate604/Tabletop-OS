#include <iostream>
#include <stdexcept>
#include "../arduino/TabletopOS_Lower_Controller/TabletopOS_Lower_Controller.ino"

void Require(bool value, const char* detail) { if (!value) throw std::runtime_error(detail); }
bool Has(const char* text) { return Serial.output.find(text) != std::string::npos; }
void Command(const std::string& text) { Serial.feed(text + "\n"); serviceSerialInput(); }
void ResetReferenced() {
  fakeReadHook = nullptr;
  std::fill_n(fakePins, 64, HIGH);
  fakeMicros = 1000000;
  Serial.input.clear(); Serial.output.clear();
  clearPlanner(); servoOff();
  mode = MODE_IDLE; faultCode = FAULT_NONE;
  positionReferenced = true; enableMotors();
  positionA = positionB = 0;
  setAxisCoordinate(0, 20); setAxisCoordinate(1, 20);
  commandedTargetX = commandedTargetY = 20;
  activeId = lastTaskId = lastTargetSequence = cancelledTaskId = 0;
  inputLength = 0; discardInputLine = false;
  for (int axis = 0; axis < 2; ++axis) {
    rawLimit[axis] = stableLimit[axis] = false;
    limitChangedAt[axis] = millis();
  }
  hostWatchdogEnabled = hostWatchdogTripped = telemetryEnabled = false;
  stopReason = STOP_NONE;
}
void FinishPlan() {
  for (int i = 0; plan.active && i < 100000; ++i) { fakeMicros += 100; serviceMotionPlan(); }
  Require(!plan.active, "motion planner did not finish");
}
void AdvanceClockDuringLoop() {
  // Cross a millisecond boundary after loop() samples its debounce time,
  // before serial commands record their own timestamps.
  fakeReadHook = [](int pin) -> int {
    fakeMicros += 1000;
    fakeReadHook = nullptr;
    return fakePins[pin];
  };
}

int main() {
  try {
    ResetReferenced();
    unsigned long parsed = 0;
    Require(!parseUnsignedLongStrict("4294967296", parsed), "uint32 overflow accepted");
    Require(parseUnsignedLongStrict("4294967295", parsed) && parsed == 0xFFFFFFFFUL, "max uint32 rejected");
    Require(isNewer32(1, 0xFFFFFFFFUL), "command wrap not recognized");
    Command("CONFIG");
    Require(Has("SERVO_RELEASE=118") && Has("SERVO_ENGAGE=81") &&
            Has("X_TRAVEL=298.00") && Has("Y_TRAVEL=466.00") &&
            Has("MAX_SPEED=50.00") && Has("MAX_STEP_RATE=8000") && Has("LAST_TASK_ID=0"),
            "missing travel/servo/speed configuration or command-id synchronization");

    ResetReferenced();
    Command("SERVO 80");
    Require(!actuatorServo.attached() && Has("ERROR SERVO_RANGE 0"),
            "servo command below calibrated range accepted");
    Serial.output.clear(); Command("SERVO 119");
    Require(!actuatorServo.attached() && Has("ERROR SERVO_RANGE 0"),
            "servo command above calibrated range accepted");
    Serial.output.clear(); Command("SERVO 81"); Command("SERVO 118");
    Require(actuatorServo.attached() && servoAngle == 118 && Has("OK SERVO 81") && Has("OK SERVO 118"),
            "calibrated servo endpoints rejected");

    ResetReferenced();
    Command("MOVE_TO 142 22 20 2");
    Require(mode == MODE_TASK_MOVE && plan.active && Has("ACK 142\nSTARTED 142"), "move not accepted");
    Serial.output.clear();
    Command("MOVE_TO 142 22 20 2");
    Require(Serial.output == "ACK 142\n", "duplicate active command restarted");
    FinishPlan();
    Require(mode == MODE_IDLE && std::fabs(xPosition()-22) < .001f && Has("COMPLETED 142"), "completion/position mismatch");
    Serial.output.clear(); Command("MOVE_TO 142 22 20 2");
    Require(Has("ERROR STALE_ID 142"), "completed ID replay accepted");

    ResetReferenced(); Command("MOVE_TO 1 120 120 50");
    Require(mode == MODE_TASK_MOVE && plan.active && std::fabs(plan.maxSpeed-50.0f) < .01f,
            "50 mm/s diagonal move was not accepted at the requested speed");
    FinishPlan();
    ResetReferenced(); Command("MOVE_TO 1 21 20 50.1");
    Require(!plan.active && Has("ERROR RANGE 1"), "move above 50 mm/s was accepted");
    ResetReferenced(); Command("MOVE_TO 1 297 20 2");
    Require(!plan.active && Has("ERROR RANGE 1"), "move beyond the new X soft limit was accepted");
    ResetReferenced(); Command("MOVE_TO 1 20 465 2");
    Require(!plan.active && Has("ERROR RANGE 1"), "move beyond the new Y soft limit was accepted");

    ResetReferenced(); Command("MOVE_TO 143 100 20 10");
    for (int i = 0; i < 1000; ++i) { fakeMicros += 100; serviceMotionPlan(); }
    Command("STOP"); FinishPlan();
    Require(Has("CANCELLED 143") && Has("STOPPED USER") && positionReferenced && mode == MODE_IDLE, "controlled stop broken");

    ResetReferenced(); Command("WATCHDOG ON"); Command("MOVE_TO 144 100 20 10");
    fakeMicros += 1600000; serviceHostWatchdog(millis()); FinishPlan();
    Require(Has("ERROR HOST_TIMEOUT 144") && Has("STOPPED HOST_TIMEOUT"), "host watchdog broken");

    ResetReferenced(); Command("WATCHDOG ON"); Command("HOME");
    Serial.output.clear(); Serial.feed("PING\n");
    AdvanceClockDuringLoop(); loop();
    Require(Has("PONG 1") && !Has("HOST_TIMEOUT") && mode == MODE_HOMING,
            "fresh heartbeat falsely timed out across loop millisecond boundary");
    fakeMicros += 1600000; loop();
    Require(Has("ERROR HOST_TIMEOUT 0") && mode == MODE_UNREFERENCED && !motorsEnabled,
            "real heartbeat loss during HOME did not stop motors");

    ResetReferenced(); Serial.feed("SET_TARGET 502 25 25\n");
    AdvanceClockDuringLoop(); loop();
    Require(mode == MODE_TRACKING && !Has("TARGET_TIMEOUT"),
            "fresh tracking target falsely timed out across loop millisecond boundary");

    ResetReferenced(); Serial.feed("SERVO 90\n");
    AdvanceClockDuringLoop(); loop();
    Require(actuatorServo.attached() && !Has("EVENT SERVO_TIMEOUT"),
            "fresh servo command falsely timed out across loop millisecond boundary");

    ResetReferenced(); Command("MOVE_TO 145 100 20 10");
    fakePins[X_LIMIT_PIN] = LOW; serviceMotionPlan();
    Require(mode == MODE_ERROR && !positionReferenced && !motorsEnabled && Has("ERROR LIMIT_X 145"), "raw limit not fail-safe");

    ResetReferenced(); Serial.feed("!"); serviceSerialInput();
    Require(mode == MODE_ERROR && !positionReferenced && !motorsEnabled && Has("ERROR ESTOP"), "emergency character broken");
    Command(""); Command("CLEAR_ERROR");
    Require(mode == MODE_UNREFERENCED && !positionReferenced, "error clear invented a reference");

    ResetReferenced(); Command("SERVO 90");
    Require(actuatorServo.attached(), "servo not attached");
    fakeMicros += 10001000; serviceServoTimeout(millis());
    Require(!actuatorServo.attached() && Has("EVENT SERVO_TIMEOUT"), "servo timeout broken");

    ResetReferenced(); Command("SERVO 90"); Command("HOME");
    Require(mode == MODE_HOMING && actuatorServo.attached(), "HOME dropped commanded lift hold");

    // Synthetic switches exercise the actual two-axis HOME state machine.
    fakeReadHook = [](int pin) -> int {
      if (pin == X_LIMIT_PIN) return xPosition() <= 0 ? LOW : HIGH;
      if (pin == Y_LIMIT_PIN) return yPosition() <= 0 ? LOW : HIGH;
      return fakePins[pin];
    };
    for (int i = 0; mode == MODE_HOMING && i < 1000000; ++i) {
      fakeMicros += 100;
      if (i % 20000 == 0) Command("SERVO 90");
      loop();
    }
    Require(mode == MODE_IDLE && positionReferenced && Has("COMPLETED HOME"), "two-axis homing failed");
    Require(xPosition() >= 2 && xPosition() < 3 && yPosition() >= 2 && yPosition() < 3, "home pull-off mismatch");

    ResetReferenced(); Command("SET_TARGET 501 25 25");
    Serial.output.clear(); Command("SET_TARGET 500 20 20");
    Require(Has("IGNORED_TARGET 500") && lastTargetSequence == 501, "old tracking target replaced new one");
    fakeMicros += 510000; serviceTargetTimeout(millis()); FinishPlan();
    Require(Has("STOPPED TARGET_TIMEOUT") && mode == MODE_IDLE, "tracking timeout broken");

    ResetReferenced(); Command(std::string(110, 'A'));
    Require(Has("ERROR LINE_TOO_LONG 0"), "oversized input not rejected");
    Serial.output.clear(); Command("PING");
    Require(Has("PONG 1"), "serial parser failed to recover after oversized line");

    std::cout << "Actual firmware with fake HAL tests passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n'; return 1;
  }
}
