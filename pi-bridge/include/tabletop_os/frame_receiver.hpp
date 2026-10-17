#pragma once
#include <array>
#include <boost/asio.hpp>
#include "tabletop_os/config.hpp"
#include "tabletop_os/frame_protocol.hpp"
#include "tabletop_os/shared_state.hpp"

namespace tabletop_os {

class FrameReceiver {
 public:
  FrameReceiver(boost::asio::io_context& io, const AppConfig& config, SharedState& state);
  void Start();
  void Stop();
  void SetSession(std::uint32_t session);

 private:
  void Receive();
  AppConfig config_;
  SharedState& state_;
  boost::asio::ip::udp::socket socket_;
  boost::asio::ip::udp::endpoint sender_;
  FrameAssembler assembler_;
  std::array<std::uint8_t, kFrameHeaderBytes + kFrameChunkBytes + 1> buffer_{};
  std::uint32_t session_ = 0;
  bool running_ = false;
};

}  // namespace tabletop_os
