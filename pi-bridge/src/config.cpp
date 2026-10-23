#include "tabletop_os/config.hpp"

#include <algorithm>
#include <fstream>
#include <stdexcept>
#include <limits>
#include <type_traits>
#include <cmath>

#include <nlohmann/json.hpp>

namespace tabletop_os {
namespace {

using Json = nlohmann::json;

template <typename T>
void Read(const Json& object, const char* key, T& destination) {
  const auto it = object.find(key);
  if (it != object.end()) {
    if constexpr (std::is_integral_v<T> && !std::is_same_v<T, bool>) {
      if (!it->is_number_integer()) throw std::runtime_error(std::string("Expected integer: ") + key);
      if (it->is_number_unsigned()) {
        if (it->get<std::uint64_t>() > static_cast<std::uint64_t>(std::numeric_limits<T>::max()))
          throw std::runtime_error(std::string("Integer out of range: ") + key);
      } else {
        const auto value = it->get<std::int64_t>();
        if constexpr (std::is_unsigned_v<T>) {
          if (value < 0 || static_cast<std::uint64_t>(value) > std::numeric_limits<T>::max())
            throw std::runtime_error(std::string("Integer out of range: ") + key);
        } else if (value < std::numeric_limits<T>::min() || value > std::numeric_limits<T>::max()) {
          throw std::runtime_error(std::string("Integer out of range: ") + key);
        }
      }
    }
    destination = it->get<T>();
    if constexpr (std::is_floating_point_v<T>) {
      if (!std::isfinite(destination)) throw std::runtime_error(std::string("Non-finite configuration: ") + key);
    }
  }
}

std::uint8_t ReadColor(const Json& object,
                       const char* key,
                       std::uint8_t fallback) {
  const auto it = object.find(key);
  if (it == object.end()) return fallback;
  const int value = it->get<int>();
  if (value < 0 || value > 255) {
    throw std::runtime_error(std::string("Color value out of range: ") + key);
  }
  return static_cast<std::uint8_t>(value);
}

const Json& Section(const Json& root, const char* name) {
  static const Json empty = Json::object();
  const auto it = root.find(name);
  if (it == root.end()) return empty;
  if (!it->is_object()) {
    throw std::runtime_error(std::string("Configuration section must be an object: ") + name);
  }
  return *it;
}

void Validate(const AppConfig& config) {
  if (config.network.frame_port == 0 ||
      config.network.frame_port == config.network.control_port ||
      config.network.frame_port == config.network.cursor_port) {
    throw std::runtime_error("Invalid frame port");
  }
  if ((config.display.mode != "frames" && config.display.mode != "diagnostic") ||
      (config.display.mode == "frames" && (config.display.expected_width != 64 || config.display.expected_height != 64)) ||
      config.display.frame_timeout_ms < 50 || config.display.assembly_timeout_ms < 10 ||
      config.display.assembly_timeout_ms > config.display.frame_timeout_ms ||
      config.motion.command_timeout_ms < 1000 || config.motion.stop_timeout_ms < 100 ||
      config.motion.servo_timeout_ms < 100) {
    throw std::runtime_error("Invalid display mode or timeout configuration");
  }
  if (config.network.control_port == 0 || config.network.cursor_port == 0) {
    throw std::runtime_error("Network ports must be non-zero");
  }
  if (config.network.control_port == config.network.cursor_port) {
    throw std::runtime_error("TCP control and UDP cursor ports must be different");
  }
  if (config.network.heartbeat_timeout_ms < 100) {
    throw std::runtime_error("network.heartbeat_timeout_ms is too small");
  }
  if (config.network.max_control_line_bytes < 128) {
    throw std::runtime_error("network.max_control_line_bytes is too small");
  }
  if (config.serial.baud == 0 || config.serial.reconnect_ms < 100 ||
      config.serial.handshake_delay_ms < 0 ||
      config.serial.keepalive_ms < 100 || config.serial.keepalive_ms > 750 ||
      config.serial.shutdown_grace_ms < 100 ||
      (config.serial.enabled && config.serial.device.empty())) {
    throw std::runtime_error("Invalid serial configuration");
  }
  if (!(config.board.min_x_mm < config.board.max_x_mm) ||
      !(config.board.min_y_mm < config.board.max_y_mm)) {
    throw std::runtime_error("Invalid board coordinate range");
  }
  if (config.cursor.timeout_ms < 50 || config.cursor.radius_px < 1) {
    throw std::runtime_error("Invalid cursor configuration");
  }
  if (config.trail.lifetime_ms < 1 || config.trail.max_points < 2 ||
      config.trail.minimum_step_mm < 0.0) {
    throw std::runtime_error("Invalid trail configuration");
  }
  if (config.display.render_fps < 1 || config.display.render_fps > 240 ||
      config.display.rows < 1 || config.display.cols < 1 ||
      config.display.chain_length < 1 || config.display.parallel < 1 ||
      config.display.expected_width < 1 || config.display.expected_height < 1 ||
      config.display.brightness < 1 || config.display.brightness > 100) {
    throw std::runtime_error("Invalid display configuration");
  }
  if (!(config.motion.min_x_mm <= config.motion.max_x_mm) ||
      !(config.motion.min_y_mm <= config.motion.max_y_mm) ||
      config.motion.minimum_speed_mm_s <= 0.0 ||
      config.motion.minimum_speed_mm_s > config.motion.maximum_speed_mm_s ||
      config.motion.telemetry_timeout_ms < 100) {
    throw std::runtime_error("Invalid motion safety limits");
  }
  if (config.motion.min_x_mm < config.board.min_x_mm ||
      config.motion.max_x_mm > config.board.max_x_mm ||
      config.motion.min_y_mm < config.board.min_y_mm ||
      config.motion.max_y_mm > config.board.max_y_mm) {
    throw std::runtime_error("Motion safety limits must stay inside the board range");
  }
}

}  // namespace

AppConfig LoadConfig(const std::string& path) {
  std::ifstream input(path);
  if (!input) {
    throw std::runtime_error("Unable to open configuration: " + path);
  }

  Json root;
  input >> root;
  if (!root.is_object()) {
    throw std::runtime_error("Configuration root must be a JSON object");
  }

  AppConfig config;

  const auto& network = Section(root, "network");
  Read(network, "bind_address", config.network.bind_address);
  Read(network, "control_port", config.network.control_port);
  Read(network, "cursor_port", config.network.cursor_port);
  Read(network, "frame_port", config.network.frame_port);
  Read(network, "heartbeat_timeout_ms", config.network.heartbeat_timeout_ms);
  Read(network, "max_control_line_bytes", config.network.max_control_line_bytes);

  const auto& serial = Section(root, "serial");
  Read(serial, "enabled", config.serial.enabled);
  Read(serial, "device", config.serial.device);
  Read(serial, "baud", config.serial.baud);
  Read(serial, "reconnect_ms", config.serial.reconnect_ms);
  Read(serial, "handshake_delay_ms", config.serial.handshake_delay_ms);
  Read(serial, "keepalive_ms", config.serial.keepalive_ms);
  Read(serial, "shutdown_grace_ms", config.serial.shutdown_grace_ms);

  const auto& board = Section(root, "board");
  Read(board, "min_x_mm", config.board.min_x_mm);
  Read(board, "max_x_mm", config.board.max_x_mm);
  Read(board, "min_y_mm", config.board.min_y_mm);
  Read(board, "max_y_mm", config.board.max_y_mm);
  Read(board, "flip_x", config.board.flip_x);
  Read(board, "flip_y", config.board.flip_y);

  const auto& cursor = Section(root, "cursor");
  Read(cursor, "timeout_ms", config.cursor.timeout_ms);
  Read(cursor, "radius_px", config.cursor.radius_px);
  config.cursor.red = ReadColor(cursor, "red", config.cursor.red);
  config.cursor.green = ReadColor(cursor, "green", config.cursor.green);
  config.cursor.blue = ReadColor(cursor, "blue", config.cursor.blue);

  const auto& trail = Section(root, "trail");
  Read(trail, "lifetime_ms", config.trail.lifetime_ms);
  Read(trail, "max_points", config.trail.max_points);
  Read(trail, "minimum_step_mm", config.trail.minimum_step_mm);
  config.trail.red = ReadColor(trail, "red", config.trail.red);
  config.trail.green = ReadColor(trail, "green", config.trail.green);
  config.trail.blue = ReadColor(trail, "blue", config.trail.blue);

  const auto& display = Section(root, "display");
  Read(display, "mode", config.display.mode);
  Read(display, "frame_timeout_ms", config.display.frame_timeout_ms);
  Read(display, "assembly_timeout_ms", config.display.assembly_timeout_ms);
  Read(display, "enabled", config.display.enabled);
  Read(display, "rows", config.display.rows);
  Read(display, "cols", config.display.cols);
  Read(display, "chain_length", config.display.chain_length);
  Read(display, "parallel", config.display.parallel);
  Read(display, "brightness", config.display.brightness);
  Read(display, "pwm_bits", config.display.pwm_bits);
  Read(display, "gpio_slowdown", config.display.gpio_slowdown);
  Read(display, "disable_hardware_pulsing", config.display.disable_hardware_pulsing);
  Read(display, "render_fps", config.display.render_fps);
  Read(display, "expected_width", config.display.expected_width);
  Read(display, "expected_height", config.display.expected_height);
  Read(display, "hardware_mapping", config.display.hardware_mapping);
  Read(display, "panel_type", config.display.panel_type);
  Read(display, "pixel_mapper", config.display.pixel_mapper);
  Read(display, "drop_privileges", config.display.drop_privileges);
  Read(display, "drop_user", config.display.drop_user);
  Read(display, "drop_group", config.display.drop_group);

  const auto& motion = Section(root, "motion");
  Read(motion, "allow_network_commands", config.motion.allow_network_commands);
  Read(motion, "min_x_mm", config.motion.min_x_mm);
  Read(motion, "max_x_mm", config.motion.max_x_mm);
  Read(motion, "min_y_mm", config.motion.min_y_mm);
  Read(motion, "max_y_mm", config.motion.max_y_mm);
  Read(motion, "minimum_speed_mm_s", config.motion.minimum_speed_mm_s);
  Read(motion, "maximum_speed_mm_s", config.motion.maximum_speed_mm_s);
  Read(motion, "telemetry_timeout_ms", config.motion.telemetry_timeout_ms);
  Read(motion, "command_timeout_ms", config.motion.command_timeout_ms);
  Read(motion, "stop_timeout_ms", config.motion.stop_timeout_ms);
  Read(motion, "servo_timeout_ms", config.motion.servo_timeout_ms);

  Validate(config);
  return config;
}

}  // namespace tabletop_os
