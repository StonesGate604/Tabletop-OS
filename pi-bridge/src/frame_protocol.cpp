#include "tabletop_os/frame_protocol.hpp"

#include <algorithm>
#include <cstring>
#include <boost/crc.hpp>

namespace tabletop_os {
namespace {
std::uint16_t U16(const std::uint8_t* p) {
  return static_cast<std::uint16_t>((p[0] << 8) | p[1]);
}
std::uint32_t U32(const std::uint8_t* p) {
  return (std::uint32_t(p[0]) << 24) | (std::uint32_t(p[1]) << 16) |
         (std::uint32_t(p[2]) << 8) | p[3];
}
bool Newer(std::uint32_t value, std::uint32_t previous) {
  const std::uint32_t difference = value - previous;
  return difference != 0 && difference < 0x80000000U;
}
}  // namespace

FrameAssembler::FrameAssembler(int timeout_ms)
    : timeout_(timeout_ms), pixels_(kFrameBytes), received_(kFrameChunks, false) {}

void FrameAssembler::SetSession(std::uint32_t session) {
  session_ = session;
  has_frame_ = finished_ = false;
  count_ = 0;
  std::fill(received_.begin(), received_.end(), false);
}

std::optional<std::vector<std::uint8_t>> FrameAssembler::Feed(
    const std::uint8_t* data, std::size_t size, Clock::time_point now) {
  if (!data || size < kFrameHeaderBytes || std::memcmp(data, "TOSF", 4) != 0 ||
      data[4] != 1 || data[5] != 0 || U16(data + 6) != 64 || U16(data + 8) != 64 ||
      session_ == 0 || U32(data + 10) != session_) return std::nullopt;
  const auto id = U32(data + 14);
  const auto index = U16(data + 18);
  const auto count = U16(data + 20);
  const auto crc = U32(data + 22);
  if (index >= kFrameChunks || count != kFrameChunks) return std::nullopt;
  const auto length = std::min(kFrameChunkBytes, kFrameBytes - index * kFrameChunkBytes);
  if (size != kFrameHeaderBytes + length) return std::nullopt;
  if (!has_frame_ || Newer(id, frame_id_)) {
    has_frame_ = true;
    frame_id_ = id;
    crc_ = crc;
    started_ = now;
    finished_ = false;
    count_ = 0;
    std::fill(received_.begin(), received_.end(), false);
  }
  if (id != frame_id_ || finished_ || crc != crc_) return std::nullopt;
  if (now - started_ > timeout_) {
    finished_ = true;
    return std::nullopt;
  }
  auto* destination = pixels_.data() + index * kFrameChunkBytes;
  if (received_[index]) {
    if (std::memcmp(destination, data + kFrameHeaderBytes, length) != 0) finished_ = true;
    return std::nullopt;
  }
  std::memcpy(destination, data + kFrameHeaderBytes, length);
  received_[index] = true;
  ++count_;
  if (count_ != kFrameChunks) return std::nullopt;
  finished_ = true;
  boost::crc_32_type checksum;
  checksum.process_bytes(pixels_.data(), pixels_.size());
  if (checksum.checksum() != crc_) return std::nullopt;
  return pixels_;
}

}  // namespace tabletop_os
