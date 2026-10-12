#pragma once

#include <cstdint>
#include <memory>
#include <string>

#include <boost/asio.hpp>
#include <nlohmann/json.hpp>

#include "tessera/arduino_link.hpp"
#include "tessera/config.hpp"
#include "tessera/control_server.hpp"
#include "tessera/cursor_receiver.hpp"
#include "tessera/shared_state.hpp"

namespace tessera {

class Application {
 public:
  Application(boost::asio::io_context& io,
              const AppConfig& config,
              SharedState& state);
  ~Application();

  void Start();
  void Stop();

 private:
  void HandleControlMessage(const nlohmann::json& message);
  void HandleControlConnection(bool connected);
  void HandleCursorMessage(const nlohmann::json& message);
  void HandleSerialLine(const std::string& line);
  void HandleSerialConnection(bool connected);

  void ScheduleMaintenance();
  void ScheduleArduinoHandshake(int delay_ms);
  void SendArduinoHandshake();
  void ConfigureArduinoSession();
  bool SendSafetyStop();
  void FinishStop();
  void ClearPendingMotion();
  void SendStatus(const std::string& reply_to = {});
  void SendError(const std::string& reply_to,
                 const std::string& code,
                 const std::string& detail = {});
  nlohmann::json BuildStatusMessage() const;
  bool MotionLeaseValid() const;
  bool TelemetryFresh(const MotionState& motion) const;
  bool MotionCommandInFlight() const;
  bool ValidateProtocolVersion(const nlohmann::json& message,
                               std::string& request_id);

  boost::asio::io_context& io_;
  const AppConfig config_;
  SharedState& state_;
  ControlServer control_server_;
  CursorReceiver cursor_receiver_;
  ArduinoLink arduino_;
  boost::asio::steady_timer maintenance_timer_;
  boost::asio::steady_timer handshake_timer_;
  boost::asio::steady_timer shutdown_timer_;

  bool running_ = false;
  bool stopping_ = false;
  bool control_socket_connected_ = false;
  bool heartbeat_received_ = false;
  bool lease_expired_ = false;
  bool stop_sent_for_expired_lease_ = false;
  bool stop_sent_for_stale_telemetry_ = false;
  bool home_in_flight_ = false;
  bool stop_in_flight_ = false;
  SteadyTime last_control_activity_{};
  SteadyTime last_arduino_keepalive_{};
  std::uint64_t next_command_id_ = 1;
  std::uint64_t pending_command_id_ = 0;
  std::string pending_request_id_;
  std::string pending_home_request_id_;
};

}  // namespace tessera
