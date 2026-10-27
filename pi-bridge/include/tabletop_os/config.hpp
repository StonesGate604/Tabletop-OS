#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace tabletop_os {

struct NetworkConfig {
  std::string bind_address = "0.0.0.0";
  std::uint16_t control_port = 7420;
  std::uint16_t cursor_port = 7421;
  std::uint16_t frame_port = 7422;
  int heartbeat_timeout_ms = 1500;
  std::size_t max_control_line_bytes = 4096;
};

struct SerialConfig {
  bool enabled = true;
  std::string device = "/dev/ttyACM0";
  unsigned int baud = 115200;
  int reconnect_ms = 1000;
  int handshake_delay_ms = 2200;
  int keepalive_ms = 400;
  int shutdown_grace_ms = 750;
};

struct BoardConfig {
  double min_x_mm = 0.0;
  double max_x_mm = 298.0;
  double min_y_mm = 0.0;
  double max_y_mm = 466.0;
  bool flip_x = false;
  bool flip_y = false;
};

struct CursorConfig {
  int timeout_ms = 250;
  int radius_px = 4;
  std::uint8_t red = 0;
  std::uint8_t green = 140;
  std::uint8_t blue = 255;
};

struct TrailConfig {
  int lifetime_ms = 1500;
  std::size_t max_points = 128;
  double minimum_step_mm = 0.25;
  std::uint8_t red = 0;
  std::uint8_t green = 0;
  std::uint8_t blue = 220;
};

struct DisplayConfig {
  std::string mode = "frames";
  int frame_timeout_ms = 250;
  int assembly_timeout_ms = 100;
  bool enabled = true;
  int rows = 32;
  int cols = 64;
  int chain_length = 2;
  int parallel = 1;
  int brightness = 30;
  int pwm_bits = 11;
  int gpio_slowdown = 4;
  bool disable_hardware_pulsing = false;
  int render_fps = 60;
  int expected_width = 64;
  int expected_height = 64;
  std::string hardware_mapping = "regular";
  std::string panel_type;
  std::string pixel_mapper = "Remap:64,64|0,0n|0,32n";
  bool drop_privileges = true;
  std::string drop_user = "tabletop-os";
  std::string drop_group = "tabletop-os";
};

struct MotionConfig {
  bool allow_network_commands = false;
  double min_x_mm = 2.0;
  double max_x_mm = 296.0;
  double min_y_mm = 2.0;
  double max_y_mm = 464.0;
  double minimum_speed_mm_s = 0.5;
  double maximum_speed_mm_s = 50.0;
  int telemetry_timeout_ms = 500;
  int command_timeout_ms = 180000;
  int stop_timeout_ms = 2500;
  int servo_timeout_ms = 1500;
};

struct AppConfig {
  NetworkConfig network;
  SerialConfig serial;
  BoardConfig board;
  CursorConfig cursor;
  TrailConfig trail;
  DisplayConfig display;
  MotionConfig motion;
};

AppConfig LoadConfig(const std::string& path);

}  // namespace tabletop_os
