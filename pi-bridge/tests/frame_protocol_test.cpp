#include "tabletop_os/frame_protocol.hpp"
#include <boost/crc.hpp>
#include <algorithm>
#include <iostream>
#include <stdexcept>
#include <string>

using namespace tabletop_os;

void Require(bool condition, const char* message) {
  if (!condition) throw std::runtime_error(message);
}
void Put16(std::vector<std::uint8_t>& bytes, std::size_t offset, std::uint16_t value) {
  bytes[offset] = static_cast<std::uint8_t>(value >> 8);
  bytes[offset + 1] = static_cast<std::uint8_t>(value);
}
void Put32(std::vector<std::uint8_t>& bytes, std::size_t offset, std::uint32_t value) {
  for (int i = 0; i < 4; ++i) bytes[offset + i] = static_cast<std::uint8_t>(value >> (24 - i * 8));
}
std::vector<std::vector<std::uint8_t>> Packets(const std::vector<std::uint8_t>& rgb,
                                             std::uint32_t session, std::uint32_t id) {
  boost::crc_32_type checksum;
  checksum.process_bytes(rgb.data(), rgb.size());
  std::vector<std::vector<std::uint8_t>> result;
  for (std::size_t i = 0; i < kFrameChunks; ++i) {
    const auto length = std::min(kFrameChunkBytes, kFrameBytes - i * kFrameChunkBytes);
    std::vector<std::uint8_t> packet(kFrameHeaderBytes + length);
    std::copy_n("TOSF", 4, packet.begin());
    packet[4] = 1;
    Put16(packet, 6, kFrameWidth); Put16(packet, 8, kFrameHeight);
    Put32(packet, 10, session); Put32(packet, 14, id);
    Put16(packet, 18, static_cast<std::uint16_t>(i));
    Put16(packet, 20, static_cast<std::uint16_t>(kFrameChunks));
    Put32(packet, 22, checksum.checksum());
    std::copy_n(rgb.begin() + i * kFrameChunkBytes, length, packet.begin() + kFrameHeaderBytes);
    result.push_back(std::move(packet));
  }
  return result;
}

int main() {
  try {
    std::vector<std::uint8_t> rgb(kFrameBytes);
    for (std::size_t i = 0; i < rgb.size(); ++i) rgb[i] = static_cast<std::uint8_t>(i % 251);
    FrameAssembler a;
    a.SetSession(123);
    const auto start = FrameAssembler::Clock::now();
    auto packets = Packets(rgb, 123, 0xFFFFFFFFU);
    std::optional<std::vector<std::uint8_t>> result;
    for (auto it = packets.rbegin(); it != packets.rend(); ++it) {
      auto complete = a.Feed(it->data(), it->size(), start);
      if (complete) result = std::move(complete);
    }
    Require(result && *result == rgb, "out-of-order assembly must reproduce every byte");
    Require(!a.Feed(packets.front().data(), packets.front().size(), start), "duplicate complete frame");
    auto next = Packets(rgb, 123, 0);
    result.reset();
    for (auto& packet : next) result = a.Feed(packet.data(), packet.size(), start);
    Require(result && *result == rgb, "uint32 frame wrap");
    Require(!a.Feed(packets.front().data(), packets.front().size(), start), "old wrapped frame");

    a.SetSession(123);
    a.Feed(next.front().data(), next.front().size(), start);
    for (std::size_t i = 1; i < next.size(); ++i)
      Require(!a.Feed(next[i].data(), next[i].size(), start + std::chrono::milliseconds(101)), "expired partial frame");
    auto newer = Packets(rgb, 123, 1);
    newer.back().back() ^= 1;
    for (auto& packet : newer) Require(!a.Feed(packet.data(), packet.size(), start), "CRC corruption");
    a.SetSession(999);
    for (auto& packet : next) Require(!a.Feed(packet.data(), packet.size(), start), "wrong session");
    a.SetSession(0);
    for (auto& packet : next) Require(!a.Feed(packet.data(), packet.size(), start), "closed session");
    a.SetSession(123);
    Require(!a.Feed(next[0].data(), 3, start), "truncated header");
    Require(!a.Feed(next[0].data(), next[0].size() - 1, start), "truncated payload");
    std::cout << "Frame protocol tests passed\n";
    return 0;
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
