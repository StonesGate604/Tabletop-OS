#include "tabletop_os/application.hpp"

#include <algorithm>
#include <chrono>
#include <cmath>
#include <iostream>
#include <limits>
#include <sstream>
#include <utility>
#include <vector>

namespace tabletop_os {
namespace {

using Json = nlohmann::json;

std::vector<std::string> SplitWords(const std::string& line) {
  std::istringstream input(line);
  std::vector<std::string> words;
  std::string word;
  while (input >> word) words.push_back(std::move(word));
  return words;
}

std::string RequestId(const Json& message) {
  const auto it = message.find("request_id");
  if (it == message.end()) return {};
  if (it->is_string()) return it->get<std::string>();
  if (it->is_number_integer() || it->is_number_unsigned()) return it->dump();
  return {};
}

bool ReadFiniteNumber(const Json& message, const char* key, double& value) {
  const auto it = message.find(key);
  if (it == message.end() || !it->is_number()) return false;
  value = it->get<double>();
  return std::isfinite(value);
}

std::int64_t AgeMilliseconds(SteadyTime timestamp, SteadyTime now) {
  if (timestamp == SteadyTime{}) return -1;
  return std::chrono::duration_cast<std::chrono::milliseconds>(now - timestamp).count();
}

bool IsHardwareFaultCode(const std::string& code) {
  return code == "ESTOP" || code == "LIMIT_X" || code == "LIMIT_Y" ||
         code == "HOME_X_NOT_FOUND" || code == "HOME_Y_NOT_FOUND" ||
         code == "HOME_X_STUCK" || code == "HOME_Y_STUCK";
}

}  // namespace

Application::Application(boost::asio::io_context& io,
                         const AppConfig& config,
                         SharedState& state)
    : io_(io),
      config_(config),
      state_(state),
      control_server_(
          io,
          config.network,
          [this](const Json& message) { HandleControlMessage(message); },
          [this](bool connected) { HandleControlConnection(connected); }),
      cursor_receiver_(
          io,
          config.network,
          [this](const Json& message) { HandleCursorMessage(message); }),
      frame_receiver_(io, config, state),
      arduino_(
          io,
          config.serial,
          [this](const std::string& line) { HandleSerialLine(line); },
          [this](bool connected) { HandleSerialConnection(connected); }),
      maintenance_timer_(io),
      handshake_timer_(io),
      shutdown_timer_(io) {}

Application::~Application() { Stop(); }

void Application::Start() {
  if (running_) return;
  stopping_ = false;
  running_ = true;
  control_server_.Start();
  cursor_receiver_.Start();
  frame_receiver_.Start();
  arduino_.Start();
  ScheduleMaintenance();
  std::cout << "Tabletop OS bridge listening: TCP " << config_.network.control_port
            << ", UDP " << config_.network.cursor_port << std::endl;
  if (!config_.motion.allow_network_commands) {
    std::cout << "Motion safety lock is ON: HOME and MOVE_TO are disabled" << std::endl;
  }
}

void Application::Stop() {
  if (!running_ || stopping_) return;
  stopping_ = true;
  running_ = false;
  maintenance_timer_.cancel();
  handshake_timer_.cancel();
  state_.HideCursor();

  const bool wait_for_serial_stop = arduino_.IsConnected();
  if (wait_for_serial_stop) {
    // Send first, then allow the Asio strand enough time to put this short
    // emergency command on the wire before closing the serial descriptor.
    SendSafetyStop();
  }
  cursor_receiver_.Stop();
  frame_receiver_.Stop();
  control_server_.Stop();

  if (!wait_for_serial_stop) {
    FinishStop();
    return;
  }
  shutdown_timer_.expires_after(
      std::chrono::milliseconds(config_.serial.shutdown_grace_ms));
  shutdown_timer_.async_wait([this](const boost::system::error_code& error) {
    if (!error) FinishStop();
  });
}

void Application::HandleControlMessage(const Json& message) {
  std::string request_id;
  if (!ValidateProtocolVersion(message, request_id)) return;
  const auto type_it = message.find("type");
  if (type_it == message.end() || !type_it->is_string()) {
    SendError(request_id, "BAD_MESSAGE", "Missing string field: type");
    return;
  }
  const std::string type = type_it->get<std::string>();

  if (type == "heartbeat") {
    last_control_activity_ = std::chrono::steady_clock::now();
    heartbeat_received_ = true;
    lease_expired_ = false;
    stop_sent_for_expired_lease_ = false;
    control_server_.Send({{"v", 1}, {"type", "heartbeat.ack"},
                          {"reply_to", request_id}});
    return;
  }
  if (type == "ping") {
    Json response = BuildStatusMessage();
    response["type"] = "pong";
    if (!request_id.empty()) response["reply_to"] = request_id;
    control_server_.Send(std::move(response));
    return;
  }
  if (type == "status.get") {
    SendStatus(request_id);
    return;
  }
  if (type == "display.open") {
    if (!MotionLeaseValid()) {
      SendError(request_id, "CONTROL_LEASE_EXPIRED");
      return;
    }
    const auto session = message.find("session");
    if (session == message.end() || !session->is_number_unsigned() ||
        session->get<std::uint64_t>() == 0 || session->get<std::uint64_t>() > 0xFFFFFFFFULL) {
      SendError(request_id, "BAD_MESSAGE", "session must be a nonzero uint32");
      return;
    }
    frame_receiver_.SetSession(session->get<std::uint32_t>());
    control_server_.Send({{"v", 1}, {"type", "display.opened"},
                          {"reply_to", request_id}, {"session", *session}});
    return;
  }
  if (type == "actuator.servo" || type == "actuator.servo_off") {
    const auto motion = state_.MotionSnapshot();
    if (type == "actuator.servo_off") {
      servo_angle_ = -1;
      pending_servo_request_id_.clear();
      if (!arduino_.IsConnected()) { SendError(request_id, "ARDUINO_DISCONNECTED"); return; }
      arduino_.SendLine("SERVO_OFF");
      control_server_.Send({{"v", 1}, {"type", "ack"}, {"reply_to", request_id}, {"status", "SERVO_OFF_SENT"}});
      return;
    }
    if (!config_.motion.allow_network_commands) { SendError(request_id, "MOTION_DISABLED"); return; }
    if (!MotionLeaseValid()) { SendError(request_id, "CONTROL_LEASE_EXPIRED"); return; }
    if (MotionCommandInFlight()) { SendError(request_id, "COMMAND_PENDING"); return; }
    if (!command_id_synchronized_) { SendError(request_id, "COMMAND_ID_NOT_SYNCHRONIZED"); return; }
    if (!motion.arduino_connected || !motion.arduino_ready || !motion.firmware_compatible ||
        !motion.config_confirmed || !motion.watchdog_enabled ||
        !TelemetryFresh(motion) || (motion.mode != "IDLE" && motion.mode != "UNREFERENCED") || motion.fault != "NONE") {
      SendError(request_id, "ARDUINO_NOT_READY"); return;
    }
    const auto angle = message.find("angle");
    if (angle == message.end() || !angle->is_number_integer() ||
        angle->get<int>() < 0 || angle->get<int>() > 180) {
      SendError(request_id, "RANGE"); return;
    }
    if (!RememberRequest(request_id)) return;
    servo_angle_ = angle->get<int>();
    pending_servo_request_id_ = request_id;
    servo_started_at_ = last_servo_keepalive_ = std::chrono::steady_clock::now();
    arduino_.SendLine("SERVO " + std::to_string(servo_angle_));
    return;
  }
  if (type == "motion.stop") {
    if (!arduino_.IsConnected()) {
      SendError(request_id, "ARDUINO_DISCONNECTED");
      return;
    }
    const bool sent = SendSafetyStop();
    control_server_.Send({{"v", 1}, {"type", "ack"},
                          {"reply_to", request_id},
                          {"status", sent ? "STOP_SENT" : "STOP_PENDING"}});
    return;
  }
  if (type == "motion.home") {
    if (!config_.motion.allow_network_commands) {
      SendError(request_id, "MOTION_DISABLED");
      return;
    }
    if (!MotionLeaseValid()) {
      SendError(request_id, "CONTROL_LEASE_EXPIRED");
      return;
    }
    if (MotionCommandInFlight()) {
      SendError(request_id, "COMMAND_PENDING");
      return;
    }
    if (!command_id_synchronized_) {
      SendError(request_id, "COMMAND_ID_NOT_SYNCHRONIZED");
      return;
    }
    const auto motion = state_.MotionSnapshot();
    if (!motion.arduino_connected) {
      SendError(request_id, "ARDUINO_DISCONNECTED");
      return;
    }
    if (!motion.arduino_ready || !motion.firmware_compatible) {
      SendError(request_id, "ARDUINO_NOT_READY");
      return;
    }
    if (!motion.config_confirmed) {
      SendError(request_id, "ARDUINO_CONFIG_UNCONFIRMED");
      return;
    }
    if (!motion.watchdog_enabled) {
      SendError(request_id, "ARDUINO_WATCHDOG_OFF");
      return;
    }
    if (!TelemetryFresh(motion)) {
      SendError(request_id, "TELEMETRY_STALE");
      return;
    }
    if (motion.fault != "NONE") {
      SendError(request_id, "ARDUINO_FAULT", motion.fault);
      return;
    }
    if (motion.mode != "IDLE" && motion.mode != "UNREFERENCED") {
      SendError(request_id, "BUSY");
      return;
    }
    if (!RememberRequest(request_id)) return;
    command_started_at_ = std::chrono::steady_clock::now();
    home_in_flight_ = true;
    pending_home_request_id_ = request_id;
    arduino_.SendLine("HOME");
    return;
  }
  if (type == "motion.move_to") {
    if (!config_.motion.allow_network_commands) {
      SendError(request_id, "MOTION_DISABLED");
      return;
    }
    if (!MotionLeaseValid()) {
      SendError(request_id, "CONTROL_LEASE_EXPIRED");
      return;
    }
    if (MotionCommandInFlight()) {
      SendError(request_id, "COMMAND_PENDING");
      return;
    }
    const auto motion = state_.MotionSnapshot();
    if (!motion.arduino_connected) {
      SendError(request_id, "ARDUINO_DISCONNECTED");
      return;
    }
    if (!motion.arduino_ready || !motion.firmware_compatible) {
      SendError(request_id, "ARDUINO_NOT_READY");
      return;
    }
    if (!motion.config_confirmed) {
      SendError(request_id, "ARDUINO_CONFIG_UNCONFIRMED");
      return;
    }
    if (!motion.watchdog_enabled) {
      SendError(request_id, "ARDUINO_WATCHDOG_OFF");
      return;
    }
    if (!TelemetryFresh(motion)) {
      SendError(request_id, "TELEMETRY_STALE");
      return;
    }
    if (motion.fault != "NONE") {
      SendError(request_id, "ARDUINO_FAULT", motion.fault);
      return;
    }
    if (!motion.referenced) {
      SendError(request_id, "NOT_HOMED");
      return;
    }
    if (motion.mode != "IDLE") {
      SendError(request_id, "BUSY");
      return;
    }
    if (!command_id_synchronized_) {
      SendError(request_id, "COMMAND_ID_NOT_SYNCHRONIZED");
      return;
    }

    double x = 0.0;
    double y = 0.0;
    double speed = 0.0;
    if (!ReadFiniteNumber(message, "x_mm", x) ||
        !ReadFiniteNumber(message, "y_mm", y) ||
        !ReadFiniteNumber(message, "speed_mm_s", speed)) {
      SendError(request_id, "BAD_MESSAGE", "MOVE_TO requires finite x_mm, y_mm and speed_mm_s");
      return;
    }
    if (x < config_.motion.min_x_mm || x > config_.motion.max_x_mm ||
        y < config_.motion.min_y_mm || y > config_.motion.max_y_mm ||
        speed < config_.motion.minimum_speed_mm_s ||
        speed > config_.motion.maximum_speed_mm_s) {
      SendError(request_id, "RANGE");
      return;
    }

    if (!RememberRequest(request_id)) return;
    command_started_at_ = std::chrono::steady_clock::now();
    constexpr std::uint64_t kMaxArduinoId = 0xFFFFFFFFULL;
    const std::uint64_t command_id = next_command_id_;
    next_command_id_ =
        next_command_id_ >= kMaxArduinoId ? 1 : next_command_id_ + 1;
    pending_command_id_ = command_id;
    pending_request_id_ = request_id;

    std::ostringstream command;
    command << "MOVE_TO " << command_id << ' ' << x << ' ' << y << ' ' << speed;
    arduino_.SendLine(command.str());
    return;
  }

  SendError(request_id, "UNKNOWN_TYPE", type);
}

void Application::HandleControlConnection(bool connected) {
  control_socket_connected_ = connected;
  state_.SetControlClientConnected(connected);
  if (connected) {
    last_control_activity_ = std::chrono::steady_clock::now();
    heartbeat_received_ = false;
    lease_expired_ = false;
    stop_sent_for_expired_lease_ = false;
    return;
  }

  heartbeat_received_ = false;
  frame_receiver_.SetSession(0);
  state_.HideCursor();
  if (!stopping_ && arduino_.IsConnected()) {
    // STOP is idempotent in the lower controller. Do not depend on possibly
    // stale telemetry to decide whether the machine could still be moving.
    SendSafetyStop();
  }
}

void Application::HandleCursorMessage(const Json& message) {
  try {
    if (!message.is_object() || message.value("v", 1) != 1 ||
        message.value("type", std::string{}) != "cursor") {
      return;
    }
    const auto sequence_it = message.find("seq");
    const auto visible_it = message.find("visible");
    if (sequence_it == message.end() || !sequence_it->is_number_unsigned() ||
        visible_it == message.end() || !visible_it->is_boolean()) {
      return;
    }

    CursorState cursor;
    cursor.sequence = sequence_it->get<std::uint64_t>();
    cursor.has_sequence = true;
    cursor.visible = visible_it->get<bool>();
    cursor.received_at = std::chrono::steady_clock::now();
    cursor.gesture = message.value("gesture", std::string("none"));
    cursor.confidence = message.value("confidence", 0.0);
    if (!std::isfinite(cursor.confidence)) cursor.confidence = 0.0;

    if (cursor.visible) {
      if (!ReadFiniteNumber(message, "x_mm", cursor.x_mm) ||
          !ReadFiniteNumber(message, "y_mm", cursor.y_mm)) {
        return;
      }
      if (cursor.x_mm < config_.board.min_x_mm ||
          cursor.x_mm > config_.board.max_x_mm ||
          cursor.y_mm < config_.board.min_y_mm ||
          cursor.y_mm > config_.board.max_y_mm) {
        cursor.visible = false;
      }
    }
    state_.UpdateCursor(cursor);
  } catch (const std::exception& error) {
    std::cerr << "Rejected cursor message: " << error.what() << std::endl;
  }
}

void Application::HandleSerialLine(const std::string& line) {
  state_.SetLastSerialLine(line);
  const auto words = SplitWords(line);
  if (words.empty()) return;

  try {
    if (words[0] == "READY") {
      const bool known_identity = words.size() >= 3 &&
                                  (words[1] == "TABLETOP_OS_LOWER" ||
                                   words[1] == "TESSERA_LOWER");
      const bool compatible = known_identity && words[2] == "1";
      handshake_timer_.cancel();
      state_.BeginArduinoSession();
      command_id_synchronized_ = false;
      servo_angle_ = -1;
      pending_servo_request_id_.clear();
      ClearPendingMotion();
      stop_in_flight_ = false;
      stop_sent_for_stale_telemetry_ = false;
      state_.SetFirmwareCompatible(compatible);
      state_.SetArduinoReady(compatible);
      // A USB reconnect or an in-place MCU reset can leave a pre-existing
      // move running. STOP must precede keepalive/configuration so reconnecting
      // cannot accidentally keep that old motion alive.
      SendSafetyStop();
      if (!stopping_ && compatible) {
        ConfigureArduinoSession();
      }
    } else if (words[0] == "PONG") {
      state_.SetArduinoReady(words.size() >= 2 && words[1] == "1");
    } else if (words[0] == "STATE" && words.size() == 8) {
      const auto actual_x = std::stod(words[4]);
      const auto actual_y = std::stod(words[5]);
      const auto target_x = std::stod(words[6]);
      const auto target_y = std::stod(words[7]);
      static const std::vector<std::string> modes = {
          "UNREFERENCED", "HOMING", "IDLE", "TASK_MOVE", "TRACKING", "STOPPING", "ERROR"};
      if (!std::isfinite(actual_x) || !std::isfinite(actual_y) ||
          !std::isfinite(target_x) || !std::isfinite(target_y) ||
          std::find(modes.begin(), modes.end(), words[2]) == modes.end()) {
        throw std::runtime_error("Invalid STATE values or mode");
      }
      state_.UpdateMotion(std::stoull(words[1]), words[2], std::stoull(words[3]),
                          actual_x, actual_y, target_x, target_y,
                          std::chrono::steady_clock::now());
      control_server_.Send(BuildStatusMessage(), true);
    } else if (words[0] == "IO") {
      for (std::size_t i = 1; i < words.size(); ++i) {
        const auto separator = words[i].find('=');
        if (separator == std::string::npos) continue;
        const std::string key = words[i].substr(0, separator);
        const std::string value = words[i].substr(separator + 1);
        if (key == "REFERENCED") state_.SetReferenced(value == "1");
        if (key == "WATCHDOG") state_.SetWatchdogEnabled(value == "ON");
        if (key == "FAULT") state_.SetFault(value);
      }
      control_server_.Send({{"v", 1}, {"type", "arduino.io"}, {"raw", line}});
    } else if ((words[0] == "ACK" || words[0] == "STARTED") &&
               words.size() >= 2) {
      if (words[1] == "HOME") {
        Json event = {{"v", 1},
                      {"type", words[0] == "ACK" ? "motion.accepted" : "motion.started"},
                      {"motion", "HOME"}};
        if (home_in_flight_ && !pending_home_request_id_.empty()) {
          event["reply_to"] = pending_home_request_id_;
        }
        control_server_.Send(std::move(event));
      } else {
        const auto command_id = std::stoull(words[1]);
        Json event = {{"v", 1},
                      {"type", words[0] == "ACK" ? "motion.accepted" : "motion.started"},
                      {"command_id", command_id}};
        if (command_id == pending_command_id_ && !pending_request_id_.empty()) {
          event["reply_to"] = pending_request_id_;
        }
        control_server_.Send(std::move(event));
      }
    } else if (words[0] == "COMPLETED" && words.size() >= 2) {
      if (words[1] == "HOME") {
        state_.SetReferenced(true);
        control_server_.Send({{"v", 1}, {"type", "motion.completed"},
                              {"reply_to", pending_home_request_id_},
                              {"motion", "HOME"}});
        home_in_flight_ = false;
        pending_home_request_id_.clear();
      } else {
        const auto command_id = std::stoull(words[1]);
        Json event = {{"v", 1}, {"type", "motion.completed"}, {"command_id", command_id}};
        if (command_id == pending_command_id_ && !pending_request_id_.empty()) event["reply_to"] = pending_request_id_;
        control_server_.Send(std::move(event));
        if (command_id == pending_command_id_) {
          pending_command_id_ = 0;
          pending_request_id_.clear();
        }
      }
    } else if (words[0] == "CANCELLED" && words.size() >= 2) {
      const auto command_id = std::stoull(words[1]);
      Json event = {{"v", 1}, {"type", "motion.cancelled"},
                    {"command_id", command_id}};
      if (command_id == pending_command_id_ && !pending_request_id_.empty()) {
        event["reply_to"] = pending_request_id_;
        ClearPendingMotion();
      }
      control_server_.Send(std::move(event));
    } else if (words[0] == "STOPPED" && words.size() >= 2) {
      control_server_.Send({{"v", 1}, {"type", "motion.stopped"},
                            {"reason", words[1]}});
      stop_in_flight_ = false;
      ClearPendingMotion();
      if (stopping_) FinishStop();
    } else if (words[0] == "ERROR" && words.size() >= 2) {
      if (IsHardwareFaultCode(words[1])) {
        state_.SetFault(words[1]);
        state_.SetReferenced(false);
        servo_angle_ = -1;
      }
      std::uint64_t related_id = 0;
      if (words.size() >= 3) related_id = std::stoull(words[2]);
      Json error = {{"v", 1}, {"type", "arduino.error"}, {"code", words[1]},
                    {"related_id", related_id}, {"raw", line}};
      if (related_id != 0 && related_id == pending_command_id_) {
        if (!pending_request_id_.empty()) error["reply_to"] = pending_request_id_;
        ClearPendingMotion();
      } else if (related_id == 0 && home_in_flight_) {
        if (!pending_home_request_id_.empty()) {
          error["reply_to"] = pending_home_request_id_;
        }
        home_in_flight_ = false;
        pending_home_request_id_.clear();
      }
      control_server_.Send(std::move(error));
      if (!pending_servo_request_id_.empty()) {
        SendError(pending_servo_request_id_, words[1]);
        pending_servo_request_id_.clear();
        servo_angle_ = -1;
      }
    } else if (words[0] == "OK" && words.size() == 3 && words[1] == "SERVO") {
      if (!pending_servo_request_id_.empty() && std::stoi(words[2]) == servo_angle_) {
        control_server_.Send({{"v", 1}, {"type", "actuator.completed"},
                              {"reply_to", pending_servo_request_id_}, {"angle", servo_angle_}});
        pending_servo_request_id_.clear();
      }
    } else if (words[0] == "CONFIG") {
      for (std::size_t i = 1; i < words.size(); ++i) {
        const auto separator = words[i].find('=');
        if (separator == std::string::npos) continue;
        if (words[i].substr(0, separator) == "CONFIRMED") {
          state_.SetConfigConfirmed(words[i].substr(separator + 1) == "1");
        }
        if (words[i].substr(0, separator) == "LAST_TASK_ID") {
          const auto last_id = std::stoull(words[i].substr(separator + 1));
          if (last_id <= 0xFFFFFFFFULL) {
            next_command_id_ = last_id >= 0xFFFFFFFFULL ? 1 : last_id + 1;
            command_id_synchronized_ = true;
          }
        }
      }
      control_server_.Send({{"v", 1}, {"type", "arduino.config"}, {"raw", line}});
    } else {
      control_server_.Send({{"v", 1}, {"type", "arduino.line"}, {"raw", line}}, true);
    }
  } catch (const std::exception& error) {
    std::cerr << "Unable to parse Arduino line '" << line << "': "
              << error.what() << std::endl;
  }
}

void Application::HandleSerialConnection(bool connected) {
  state_.SetArduinoConnected(connected);
  stop_sent_for_stale_telemetry_ = false;
  if (connected) {
    last_arduino_keepalive_ = std::chrono::steady_clock::now();
    handshake_attempts_ = 0;
    SendSafetyStop();
    ScheduleArduinoHandshake(config_.serial.handshake_delay_ms);
  } else {
    handshake_timer_.cancel();
    stop_in_flight_ = false;
    command_id_synchronized_ = false;
    servo_angle_ = -1;
    pending_servo_request_id_.clear();
    ClearPendingMotion();
    control_server_.Send({{"v", 1}, {"type", "arduino.connection"},
                          {"connected", false}});
    if (stopping_) FinishStop();
  }
}

void Application::ScheduleMaintenance() {
  if (!running_) return;
  maintenance_timer_.expires_after(std::chrono::milliseconds(50));
  maintenance_timer_.async_wait([this](const boost::system::error_code& error) {
    if (error || !running_) return;
    const auto now = std::chrono::steady_clock::now();
    state_.ExpireCursor(now);

    const auto lease_timeout =
        std::chrono::milliseconds(config_.network.heartbeat_timeout_ms);
    const bool lease_deadline_elapsed = control_socket_connected_ &&
        last_control_activity_ != SteadyTime{} &&
        now - last_control_activity_ > lease_timeout;
    if (control_socket_connected_ && MotionLeaseValid()) {
      lease_expired_ = false;
    } else if (lease_deadline_elapsed && !lease_expired_) {
      lease_expired_ = true;
      frame_receiver_.SetSession(0);
      state_.HideCursor();
      if (!stop_sent_for_expired_lease_ && arduino_.IsConnected()) {
        stop_sent_for_expired_lease_ = SendSafetyStop() || stop_in_flight_;
      }
      control_server_.Send({{"v", 1}, {"type", "control.lease_expired"}});
    }

    if (arduino_.IsConnected() && !stop_in_flight_ &&
        now - last_arduino_keepalive_ >=
            std::chrono::milliseconds(config_.serial.keepalive_ms)) {
      arduino_.SendLine("PING");
      last_arduino_keepalive_ = now;
    }

    const auto motion = state_.MotionSnapshot();
    if ((home_in_flight_ || pending_command_id_ != 0) && command_started_at_ != SteadyTime{} &&
        now - command_started_at_ > std::chrono::milliseconds(config_.motion.command_timeout_ms)) {
      SendError(home_in_flight_ ? pending_home_request_id_ : pending_request_id_, "COMMAND_TIMEOUT");
      ClearPendingMotion();
      SendSafetyStop();
    }
    if (!pending_servo_request_id_.empty() && now - servo_started_at_ >
        std::chrono::milliseconds(config_.motion.servo_timeout_ms)) {
      SendError(pending_servo_request_id_, "SERVO_TIMEOUT");
      pending_servo_request_id_.clear();
      SendSafetyStop();
    }
    if (stop_in_flight_ && now - stop_started_at_ >
        std::chrono::milliseconds(config_.motion.stop_timeout_ms)) {
      control_server_.Send({{"v", 1}, {"type", "motion.interlock"}, {"reason", "STOP_TIMEOUT"}});
      // Leave the lease stopped and close serial. Independent MCU watchdog remains the fallback.
      std::cerr << "Arduino STOP timeout; serial disabled until service restart" << std::endl;
      arduino_.Stop();
      ScheduleMaintenance();
      return;
    }
    if (servo_angle_ >= 0 && pending_servo_request_id_.empty() && MotionLeaseValid() &&
        TelemetryFresh(motion) && motion.fault == "NONE" && !stop_in_flight_ &&
        now - last_servo_keepalive_ >= std::chrono::seconds(2)) {
      arduino_.SendLine("SERVO " + std::to_string(servo_angle_));
      last_servo_keepalive_ = now;
    }
    const bool motion_may_be_active = motion.mode == "HOMING" ||
                                      motion.mode == "TASK_MOVE" ||
                                      motion.mode == "TRACKING" ||
                                      motion.mode == "STOPPING" ||
                                      MotionCommandInFlight();
    if (motion_may_be_active && !TelemetryFresh(motion)) {
      if (!stop_sent_for_stale_telemetry_ && arduino_.IsConnected()) {
        stop_sent_for_stale_telemetry_ = SendSafetyStop() || stop_in_flight_;
        control_server_.Send({{"v", 1},
                              {"type", "motion.interlock"},
                              {"reason", "TELEMETRY_STALE"}});
      }
    } else if (TelemetryFresh(motion)) {
      stop_sent_for_stale_telemetry_ = false;
    }
    ScheduleMaintenance();
  });
}

void Application::ScheduleArduinoHandshake(int delay_ms) {
  if (!running_ || !arduino_.IsConnected()) return;
  handshake_timer_.expires_after(std::chrono::milliseconds(std::max(0, delay_ms)));
  handshake_timer_.async_wait([this](const boost::system::error_code& error) {
    if (!error && running_ && arduino_.IsConnected()) SendArduinoHandshake();
  });
}

void Application::SendArduinoHandshake() {
  if (handshake_attempts_ >= 3) {
    std::cerr << "Arduino identity handshake timed out; motion remains locked" << std::endl;
    SendError({}, "ARDUINO_HANDSHAKE_TIMEOUT");
    return;
  }
  ++handshake_attempts_;
  arduino_.SendLine("IDENTIFY");
  ScheduleArduinoHandshake(1000);
}

void Application::ConfigureArduinoSession() {
  arduino_.SendLine("PING");
  arduino_.SendLine("CONFIG");
  arduino_.SendLine("WATCHDOG ON");
  arduino_.SendLine("STATUS");
  arduino_.SendLine("TELEMETRY ON");
}

bool Application::SendSafetyStop() {
  if (!arduino_.IsConnected() || stop_in_flight_) return false;
  stop_in_flight_ = true;
  stop_started_at_ = std::chrono::steady_clock::now();
  servo_angle_ = -1;
  if (!pending_servo_request_id_.empty()) SendError(pending_servo_request_id_, "SERVO_CANCELLED");
  pending_servo_request_id_.clear();
  arduino_.SendLine("SERVO_OFF", true);
  arduino_.SendLine("STOP", true);
  return true;
}

void Application::FinishStop() {
  shutdown_timer_.cancel();
  arduino_.Stop();
}

void Application::ClearPendingMotion() {
  pending_command_id_ = 0;
  pending_request_id_.clear();
  home_in_flight_ = false;
  pending_home_request_id_.clear();
}

void Application::SendStatus(const std::string& reply_to) {
  Json response = BuildStatusMessage();
  if (!reply_to.empty()) response["reply_to"] = reply_to;
  control_server_.Send(std::move(response));
}

void Application::SendError(const std::string& reply_to,
                            const std::string& code,
                            const std::string& detail) {
  Json response = {{"v", 1}, {"type", "error"}, {"code", code}};
  if (!reply_to.empty()) response["reply_to"] = reply_to;
  if (!detail.empty()) response["detail"] = detail;
  control_server_.Send(std::move(response));
}

Json Application::BuildStatusMessage() const {
  const auto now = std::chrono::steady_clock::now();
  const auto snapshot = state_.Snapshot(now);
  return {
      {"v", 1},
      {"type", "status"},
      {"control_connected", snapshot.motion.control_client_connected},
      {"control_lease_valid", MotionLeaseValid()},
      {"motion_commands_enabled", config_.motion.allow_network_commands},
      {"motion_command_in_flight", MotionCommandInFlight()},
      {"command_id_synchronized", command_id_synchronized_},
      {"display", {{"mode", config_.display.mode}, {"frame_seq", snapshot.frame.sequence},
                   {"session", snapshot.frame.session},
                   {"frame_age_ms", AgeMilliseconds(snapshot.frame.received_at, now)}}},
      {"cursor", {
          {"visible", snapshot.cursor.visible},
          {"seq", snapshot.cursor.sequence},
          {"x_mm", snapshot.cursor.x_mm},
          {"y_mm", snapshot.cursor.y_mm},
          {"gesture", snapshot.cursor.gesture},
          {"age_ms", AgeMilliseconds(snapshot.cursor.received_at, now)}}},
      {"arduino", {
          {"connected", snapshot.motion.arduino_connected},
          {"ready", snapshot.motion.arduino_ready},
          {"firmware_compatible", snapshot.motion.firmware_compatible},
          {"config_confirmed", snapshot.motion.config_confirmed},
          {"watchdog_enabled", snapshot.motion.watchdog_enabled},
          {"referenced", snapshot.motion.referenced},
          {"mode", snapshot.motion.mode},
          {"active_id", snapshot.motion.active_id},
          {"actual_x_mm", snapshot.motion.actual_x_mm},
          {"actual_y_mm", snapshot.motion.actual_y_mm},
          {"target_x_mm", snapshot.motion.target_x_mm},
          {"target_y_mm", snapshot.motion.target_y_mm},
          {"fault", snapshot.motion.fault},
          {"telemetry_age_ms", AgeMilliseconds(snapshot.motion.last_telemetry_at, now)}}}
  };
}

bool Application::MotionLeaseValid() const {
  if (!control_socket_connected_ || !heartbeat_received_ ||
      last_control_activity_ == SteadyTime{}) {
    return false;
  }
  return std::chrono::steady_clock::now() - last_control_activity_ <=
      std::chrono::milliseconds(config_.network.heartbeat_timeout_ms);
}

bool Application::TelemetryFresh(const MotionState& motion) const {
  if (!motion.arduino_connected || motion.last_telemetry_at == SteadyTime{}) {
    return false;
  }
  return std::chrono::steady_clock::now() - motion.last_telemetry_at <=
      std::chrono::milliseconds(config_.motion.telemetry_timeout_ms);
}

bool Application::MotionCommandInFlight() const {
  return stop_in_flight_ || home_in_flight_ || pending_command_id_ != 0 || !pending_servo_request_id_.empty();
}

bool Application::RememberRequest(const std::string& request_id) {
  if (request_id.empty() || request_id.size() > 128) {
    SendError(request_id, "REQUEST_ID_REQUIRED");
    return false;
  }
  if (std::find(recent_requests_.begin(), recent_requests_.end(), request_id) != recent_requests_.end()) {
    SendError(request_id, "DUPLICATE_REQUEST");
    return false;
  }
  recent_requests_.push_back(request_id);
  while (recent_requests_.size() > 64) recent_requests_.pop_front();
  return true;
}

bool Application::ValidateProtocolVersion(const Json& message,
                                          std::string& request_id) {
  request_id = RequestId(message);
  if (!message.is_object()) {
    SendError(request_id, "BAD_MESSAGE", "Expected a JSON object");
    return false;
  }
  const auto version = message.find("v");
  if (version != message.end() && (!version->is_number_integer() || version->get<int>() != 1)) {
    SendError(request_id, "UNSUPPORTED_VERSION");
    return false;
  }
  return true;
}

}  // namespace tabletop_os
