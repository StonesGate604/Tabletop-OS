#include "tabletop_os/cursor_receiver.hpp"

#include <algorithm>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace tabletop_os {
namespace {

using boost::asio::ip::udp;

void LogCursor(const std::string& message) {
  std::cerr << "[cursor] " << message << '\n';
}

}  // namespace

class CursorReceiver::Impl {
 private:
  class State : public std::enable_shared_from_this<State> {
   public:
    State(boost::asio::io_context& io,
          const NetworkConfig& config,
          MessageHandler on_message)
        : io_(io),
          config_(config),
          on_message_(std::move(on_message)),
          socket_(io),
          receive_buffer_(
              std::max<std::size_t>(1, config.max_control_line_bytes) + 1) {}

    void Start() {
      if (started_) {
        return;
      }

      boost::system::error_code error;
      const auto address =
          boost::asio::ip::make_address(config_.bind_address, error);
      if (error) {
        const std::string message = "invalid bind address '" +
            config_.bind_address + "': " + error.message();
        LogCursor(message);
        throw std::runtime_error(message);
      }

      const udp::endpoint endpoint(address, config_.cursor_port);
      socket_.open(endpoint.protocol(), error);
      if (error) {
        const std::string message =
            "failed to open UDP socket: " + error.message();
        LogCursor(message);
        throw std::runtime_error(message);
      }

      socket_.set_option(boost::asio::socket_base::reuse_address(true), error);
      if (error) {
        LogCursor("failed to set SO_REUSEADDR: " + error.message());
        CloseSocket();
        throw std::runtime_error("failed to configure UDP cursor socket: " +
                                 error.message());
      }

      socket_.bind(endpoint, error);
      if (error) {
        LogCursor("failed to bind UDP " + config_.bind_address + ":" +
                  std::to_string(config_.cursor_port) + ": " +
                  error.message());
        CloseSocket();
        throw std::runtime_error("failed to bind UDP cursor port: " +
                                 error.message());
      }

      started_ = true;
      LogCursor("listening on " + config_.bind_address + ":" +
                std::to_string(config_.cursor_port));
      BeginReceive();
    }

    void Stop() {
      if (!started_ && !socket_.is_open()) {
        return;
      }
      started_ = false;
      CloseSocket();
    }

   private:
    void CloseSocket() {
      boost::system::error_code ignored;
      socket_.cancel(ignored);
      socket_.close(ignored);
    }

    void BeginReceive() {
      if (!started_) {
        return;
      }

      auto self = shared_from_this();
      socket_.async_receive_from(
          boost::asio::buffer(receive_buffer_), sender_,
          [self](const boost::system::error_code& error,
                 std::size_t bytes_received) {
            self->HandleReceive(error, bytes_received);
          });
    }

    void HandleReceive(const boost::system::error_code& error,
                       std::size_t bytes_received) {
      if (!started_) {
        return;
      }

      if (error) {
        if (error != boost::asio::error::operation_aborted) {
          LogCursor("receive failed; datagram dropped: " + error.message());
          BeginReceive();
        }
        return;
      }

      const std::size_t maximum =
          std::max<std::size_t>(1, config_.max_control_line_bytes);
      if (bytes_received > maximum) {
        LogCursor("oversized datagram dropped (maximum " +
                  std::to_string(maximum) + " bytes)");
        BeginReceive();
        return;
      }

      try {
        auto message = nlohmann::json::parse(receive_buffer_.begin(),
                                             receive_buffer_.begin() +
                                                 bytes_received);
        if (on_message_) {
          on_message_(message);
        }
      } catch (const nlohmann::json::parse_error& exception) {
        LogCursor(std::string("invalid JSON datagram dropped: ") +
                  exception.what());
      } catch (const std::exception& exception) {
        LogCursor(std::string("cursor handler failed; datagram dropped: ") +
                  exception.what());
      }

      BeginReceive();
    }

    boost::asio::io_context& io_;
    NetworkConfig config_;
    MessageHandler on_message_;
    udp::socket socket_;
    udp::endpoint sender_;
    std::vector<char> receive_buffer_;
    bool started_ = false;
  };

 public:
  Impl(boost::asio::io_context& io,
       const NetworkConfig& config,
       MessageHandler on_message)
      : state_(std::make_shared<State>(io, config, std::move(on_message))) {}

  ~Impl() { state_->Stop(); }

  void Start() { state_->Start(); }
  void Stop() { state_->Stop(); }

 private:
  std::shared_ptr<State> state_;
};

CursorReceiver::CursorReceiver(boost::asio::io_context& io,
                               const NetworkConfig& config,
                               MessageHandler on_message)
    : impl_(std::make_unique<Impl>(io, config, std::move(on_message))) {}

CursorReceiver::~CursorReceiver() = default;

void CursorReceiver::Start() { impl_->Start(); }

void CursorReceiver::Stop() { impl_->Stop(); }

}  // namespace tabletop_os
