#pragma once
// Host-only HAL. Never included by the Arduino IDE build.
#include <algorithm>
#include <cstdint>
#include <deque>
#include <iomanip>
#include <sstream>
#include <string>
#include <cmath>
using __FlashStringHelper = char;
#define F(value) value
constexpr int HIGH = 1, LOW = 0, OUTPUT = 1, INPUT_PULLUP = 2;
inline std::uint64_t fakeMicros = 1000000;
inline int fakePins[64] = {};
inline int (*fakeReadHook)(int) = nullptr;
inline unsigned long micros() { return static_cast<std::uint32_t>(fakeMicros); }
inline unsigned long millis() { return static_cast<std::uint32_t>(fakeMicros / 1000); }
inline void delayMicroseconds(unsigned long duration) { fakeMicros += duration; }
inline int digitalRead(int pin) { return fakeReadHook ? fakeReadHook(pin) : fakePins[pin]; }
inline void digitalWrite(int pin, int value) { fakePins[pin] = value; }
inline void pinMode(int, int) {}
template<class T> T constrain(T value, T minimum, T maximum) { return std::max(minimum, std::min(maximum, value)); }

struct FakeSerial {
  std::deque<char> input;
  std::string output;
  void begin(unsigned long) {}
  int available() { return static_cast<int>(input.size()); }
  int availableForWrite() { return 256; }
  char read() { char result = input.front(); input.pop_front(); return result; }
  void feed(const std::string& value) { for (char c : value) input.push_back(c); }
  template<class T> void print(T value) { std::ostringstream s; s << value; output += s.str(); }
  void print(std::uint8_t value) { print(static_cast<unsigned int>(value)); }
  void print(std::int8_t value) { print(static_cast<int>(value)); }
  void print(float value, int precision) { std::ostringstream s; s << std::fixed << std::setprecision(precision) << value; output += s.str(); }
  template<class T> void println(T value) { print(value); output += '\n'; }
  void println(float value, int precision) { print(value, precision); output += '\n'; }
  void println() { output += '\n'; }
};
inline FakeSerial Serial;
