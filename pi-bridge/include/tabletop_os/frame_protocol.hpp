#pragma once

#include <chrono>
#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace tabletop_os {

// TOSF v1: big endian, fixed RGB888 64x96; matches PC frame_protocol.py.
constexpr std::uint16_t kFrameWidth = 64;
constexpr std::uint16_t kFrameHeight = 96;
constexpr std::size_t kFrameBytes = kFrameWidth * kFrameHeight * 3;
constexpr std::size_t kFrameChunkBytes = 1200;
constexpr std::size_t kFrameChunks = (kFrameBytes + kFrameChunkBytes - 1) / kFrameChunkBytes;
constexpr std::size_t kFrameHeaderBytes = 26;

class FrameAssembler {
 public:
  using Clock = std::chrono::steady_clock;
  explicit FrameAssembler(int timeout_ms = 100);
  void SetSession(std::uint32_t session);
  std::optional<std::vector<std::uint8_t>> Feed(const std::uint8_t* data,
                                             std::size_t size,
                                             Clock::time_point now);
  std::uint32_t FrameId() const { return frame_id_; }

 private:
  std::uint32_t session_ = 0;
  std::uint32_t frame_id_ = 0;
  std::uint32_t crc_ = 0;
  bool has_frame_ = false;
  bool finished_ = false;
  Clock::time_point started_{};
  std::chrono::milliseconds timeout_;
  std::vector<std::uint8_t> pixels_;
  std::vector<bool> received_;
  std::size_t count_ = 0;
};

}  // namespace tabletop_os
