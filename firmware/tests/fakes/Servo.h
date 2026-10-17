#pragma once
#include <cstdint>
constexpr std::uint8_t INVALID_SERVO = 255;
class Servo {
 public:
  std::uint8_t attach(int, int, int) { attached_ = true; return 0; }
  void detach() { attached_ = false; }
  bool attached() const { return attached_; }
  void write(int angle) { angle_ = angle; }
 private:
  bool attached_ = false;
  int angle_ = 90;
};
