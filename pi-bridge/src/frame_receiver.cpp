#include "tabletop_os/frame_receiver.hpp"
#include <iostream>

namespace tabletop_os {

FrameReceiver::FrameReceiver(boost::asio::io_context& io, const AppConfig& config,
                             SharedState& state)
    : config_(config), state_(state), socket_(io),
      assembler_(config.display.assembly_timeout_ms) {}

void FrameReceiver::Start() {
  if (running_) return;
  const auto address = boost::asio::ip::make_address(config_.network.bind_address);
  const boost::asio::ip::udp::endpoint endpoint(address, config_.network.frame_port);
  socket_.open(endpoint.protocol());
  socket_.bind(endpoint);
  running_ = true;
  std::cout << "[frame] listening on UDP " << config_.network.frame_port << '\n';
  Receive();
}

void FrameReceiver::SetSession(std::uint32_t session) {
  session_ = session;
  assembler_.SetSession(session);
  state_.ClearFrame();
}

void FrameReceiver::Stop() {
  running_ = false;
  SetSession(0);
  boost::system::error_code ignored;
  socket_.cancel(ignored);
  socket_.close(ignored);
}

void FrameReceiver::Receive() {
  if (!running_) return;
  socket_.async_receive_from(boost::asio::buffer(buffer_), sender_,
      [this](const boost::system::error_code& error, std::size_t size) {
        if (!running_) return;
        if (!error) {
          const auto now = std::chrono::steady_clock::now();
          auto pixels = assembler_.Feed(buffer_.data(), size, now);
          if (pixels) state_.UpdateFrame(session_, assembler_.FrameId(), std::move(*pixels), now);
        }
        if (error != boost::asio::error::operation_aborted) Receive();
      });
}

}  // namespace tabletop_os
