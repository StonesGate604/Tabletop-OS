#pragma once

#include <chrono>
#include <cstdint>
#include <deque>
#include <mutex>
#include <string>

#include "tabletop_os/config.hpp"

namespace tabletop_os {

using SteadyTime = std::chrono::steady_clock::time_point;

struct CursorState {
  std::uint64_t sequence = 0;
  bool has_sequence = false;
  bool visible = false;
  double x_mm = 0.0;
  double y_mm = 0.0;
  std::string gesture = "none";
  double confidence = 0.0;
  SteadyTime received_at{};
};

struct TrailPoint {
  double x_mm = 0.0;
  double y_mm = 0.0;
  SteadyTime recorded_at{};
};

struct MotionState {
  bool control_client_connected = false;
  bool arduino_connected = false;
  bool arduino_ready = false;
  bool firmware_compatible = false;
  bool config_confirmed = false;
  bool watchdog_enabled = false;
  bool referenced = false;
  std::string mode = "DISCONNECTED";
  std::uint64_t telemetry_sequence = 0;
  std::uint64_t active_id = 0;
  double actual_x_mm = 0.0;
  double actual_y_mm = 0.0;
  double target_x_mm = 0.0;
  double target_y_mm = 0.0;
  std::string fault = "NONE";
  std::string last_serial_line;
  SteadyTime last_telemetry_at{};
};

struct StateSnapshot {
  CursorState cursor;
  MotionState motion;
  std::deque<TrailPoint> trail;
};

class SharedState {
 public:
  explicit SharedState(const AppConfig& config);

  bool UpdateCursor(const CursorState& cursor);
  void HideCursor();
  void ExpireCursor(SteadyTime now);

  void SetControlClientConnected(bool connected);
  void SetArduinoConnected(bool connected);
  void BeginArduinoSession();
  void SetArduinoReady(bool ready);
  void SetFirmwareCompatible(bool compatible);
  void SetConfigConfirmed(bool confirmed);
  void SetWatchdogEnabled(bool enabled);
  void SetReferenced(bool referenced);
  void SetFault(std::string fault);
  void SetLastSerialLine(std::string line);
  void UpdateMotion(std::uint64_t telemetry_sequence,
                    std::string mode,
                    std::uint64_t active_id,
                    double actual_x_mm,
                    double actual_y_mm,
                    double target_x_mm,
                    double target_y_mm,
                    SteadyTime received_at);

  StateSnapshot Snapshot(SteadyTime now) const;
  MotionState MotionSnapshot() const;

 private:
  void PruneTrailLocked(SteadyTime now);

  const CursorConfig cursor_config_;
  const TrailConfig trail_config_;
  mutable std::mutex mutex_;
  CursorState cursor_;
  MotionState motion_;
  std::deque<TrailPoint> trail_;
};

}  // namespace tabletop_os
