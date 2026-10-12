#include "tessera/shared_state.hpp"

#include <cmath>
#include <utility>

namespace tessera {

SharedState::SharedState(const AppConfig& config)
    : cursor_config_(config.cursor), trail_config_(config.trail) {}

bool SharedState::UpdateCursor(const CursorState& cursor) {
  std::lock_guard<std::mutex> lock(mutex_);
  const auto timeout = std::chrono::milliseconds(cursor_config_.timeout_ms);
  const bool previous_expired = !cursor_.has_sequence ||
      cursor.received_at - cursor_.received_at > timeout;
  if (!previous_expired && cursor_.has_sequence &&
      cursor.sequence <= cursor_.sequence) {
    return false;
  }
  cursor_ = cursor;
  return true;
}

void SharedState::HideCursor() {
  std::lock_guard<std::mutex> lock(mutex_);
  cursor_.visible = false;
}

void SharedState::ExpireCursor(SteadyTime now) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!cursor_.visible || !cursor_.has_sequence) return;
  if (now - cursor_.received_at >
      std::chrono::milliseconds(cursor_config_.timeout_ms)) {
    cursor_.visible = false;
  }
}

void SharedState::SetControlClientConnected(bool connected) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.control_client_connected = connected;
  if (!connected) cursor_.visible = false;
}

void SharedState::SetArduinoConnected(bool connected) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (motion_.arduino_connected != connected) {
    motion_.arduino_ready = false;
    motion_.firmware_compatible = false;
    motion_.config_confirmed = false;
    motion_.watchdog_enabled = false;
    motion_.referenced = false;
    motion_.telemetry_sequence = 0;
    motion_.active_id = 0;
    motion_.actual_x_mm = 0.0;
    motion_.actual_y_mm = 0.0;
    motion_.target_x_mm = 0.0;
    motion_.target_y_mm = 0.0;
    motion_.last_telemetry_at = SteadyTime{};
    motion_.fault = "UNKNOWN";
    trail_.clear();
  }
  motion_.arduino_connected = connected;
  if (!connected) {
    motion_.mode = "DISCONNECTED";
  } else {
    motion_.mode = "CONNECTING";
  }
}

void SharedState::BeginArduinoSession() {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.arduino_ready = false;
  motion_.firmware_compatible = false;
  motion_.config_confirmed = false;
  motion_.watchdog_enabled = false;
  motion_.referenced = false;
  motion_.telemetry_sequence = 0;
  motion_.active_id = 0;
  motion_.actual_x_mm = 0.0;
  motion_.actual_y_mm = 0.0;
  motion_.target_x_mm = 0.0;
  motion_.target_y_mm = 0.0;
  motion_.last_telemetry_at = SteadyTime{};
  motion_.fault = "UNKNOWN";
  motion_.mode = motion_.arduino_connected ? "CONNECTING" : "DISCONNECTED";
  trail_.clear();
}

void SharedState::SetArduinoReady(bool ready) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.arduino_ready = ready;
}

void SharedState::SetFirmwareCompatible(bool compatible) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.firmware_compatible = compatible;
}

void SharedState::SetConfigConfirmed(bool confirmed) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.config_confirmed = confirmed;
}

void SharedState::SetWatchdogEnabled(bool enabled) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.watchdog_enabled = enabled;
}

void SharedState::SetReferenced(bool referenced) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.referenced = referenced;
}

void SharedState::SetFault(std::string fault) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.fault = std::move(fault);
}

void SharedState::SetLastSerialLine(std::string line) {
  std::lock_guard<std::mutex> lock(mutex_);
  motion_.last_serial_line = std::move(line);
}

void SharedState::UpdateMotion(std::uint64_t telemetry_sequence,
                               std::string mode,
                               std::uint64_t active_id,
                               double actual_x_mm,
                               double actual_y_mm,
                               double target_x_mm,
                               double target_y_mm,
                               SteadyTime received_at) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (telemetry_sequence <= motion_.telemetry_sequence &&
      motion_.last_telemetry_at != SteadyTime{}) {
    return;
  }

  motion_.telemetry_sequence = telemetry_sequence;
  motion_.mode = std::move(mode);
  motion_.active_id = active_id;
  motion_.actual_x_mm = actual_x_mm;
  motion_.actual_y_mm = actual_y_mm;
  motion_.target_x_mm = target_x_mm;
  motion_.target_y_mm = target_y_mm;
  motion_.last_telemetry_at = received_at;

  const bool record_trail = motion_.mode == "TASK_MOVE" ||
                            motion_.mode == "STOPPING";
  if (record_trail) {
    bool append = trail_.empty();
    if (!append) {
      const double dx = actual_x_mm - trail_.back().x_mm;
      const double dy = actual_y_mm - trail_.back().y_mm;
      append = std::hypot(dx, dy) >= trail_config_.minimum_step_mm;
    }
    if (append) {
      trail_.push_back({actual_x_mm, actual_y_mm, received_at});
      while (trail_.size() > trail_config_.max_points) trail_.pop_front();
    }
  }
  PruneTrailLocked(received_at);
}

StateSnapshot SharedState::Snapshot(SteadyTime now) const {
  std::lock_guard<std::mutex> lock(mutex_);
  StateSnapshot result{cursor_, motion_, trail_};
  if (result.cursor.visible && result.cursor.has_sequence &&
      now - result.cursor.received_at >
          std::chrono::milliseconds(cursor_config_.timeout_ms)) {
    result.cursor.visible = false;
  }
  const auto lifetime = std::chrono::milliseconds(trail_config_.lifetime_ms);
  while (!result.trail.empty() && now - result.trail.front().recorded_at > lifetime) {
    result.trail.pop_front();
  }
  return result;
}

MotionState SharedState::MotionSnapshot() const {
  std::lock_guard<std::mutex> lock(mutex_);
  return motion_;
}

void SharedState::PruneTrailLocked(SteadyTime now) {
  const auto lifetime = std::chrono::milliseconds(trail_config_.lifetime_ms);
  while (!trail_.empty() && now - trail_.front().recorded_at > lifetime) {
    trail_.pop_front();
  }
}

}  // namespace tessera
