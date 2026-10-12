#include "tessera/control_server.hpp"

#include <algorithm>
#include <array>
#include <atomic>
#include <cstddef>
#include <deque>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

namespace tessera {
namespace {

using boost::asio::ip::tcp;

constexpr std::size_t kReadChunkBytes = 2048;
constexpr std::size_t kMaxOutgoingMessages = 64;

void LogControl(const std::string& message) {
  std::cerr << "[control] " << message << '\n';
}

std::string JsonLine(nlohmann::json message) {
  std::string result = message.dump();
  result.push_back('\n');
  return result;
}

}  // namespace

class ControlServer::Impl {
 private:
  struct OutgoingMessage {
    std::shared_ptr<std::string> data;
    bool replaceable_status = false;
  };

  class State : public std::enable_shared_from_this<State> {
   public:
    State(boost::asio::io_context& io,
          const NetworkConfig& config,
          MessageHandler on_message,
          ConnectionHandler on_connection)
        : io_(io),
          config_(config),
          on_message_(std::move(on_message)),
          on_connection_(std::move(on_connection)),
          acceptor_(io) {}

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
        LogControl(message);
        throw std::runtime_error(message);
      }

      const tcp::endpoint endpoint(address, config_.control_port);
      acceptor_.open(endpoint.protocol(), error);
      if (error) {
        const std::string message =
            "failed to open TCP listener: " + error.message();
        LogControl(message);
        throw std::runtime_error(message);
      }

      acceptor_.set_option(tcp::acceptor::reuse_address(true), error);
      if (error) {
        LogControl("failed to set SO_REUSEADDR: " + error.message());
        CloseAcceptor();
        throw std::runtime_error("failed to configure TCP listener: " +
                                 error.message());
      }

      acceptor_.bind(endpoint, error);
      if (error) {
        LogControl("failed to bind TCP " + config_.bind_address + ":" +
                   std::to_string(config_.control_port) + ": " +
                   error.message());
        CloseAcceptor();
        throw std::runtime_error("failed to bind TCP control port: " +
                                 error.message());
      }

      acceptor_.listen(boost::asio::socket_base::max_listen_connections,
                       error);
      if (error) {
        LogControl("failed to listen on TCP port: " + error.message());
        CloseAcceptor();
        throw std::runtime_error("failed to listen on TCP control port: " +
                                 error.message());
      }

      started_ = true;
      LogControl("listening on " + config_.bind_address + ":" +
                 std::to_string(config_.control_port));
      BeginAccept();
    }

    void Stop() {
      if (!started_ && !client_socket_) {
        return;
      }
      started_ = false;
      CloseAcceptor();
      DisconnectClient("server stopped");
    }

    void Send(nlohmann::json message, bool replaceable_status) {
      std::string encoded;
      try {
        encoded = JsonLine(std::move(message));
      } catch (const std::exception& exception) {
        LogControl(std::string("could not serialize outgoing JSON: ") +
                   exception.what());
        return;
      }

      auto self = shared_from_this();
      boost::asio::post(
          io_, [self, encoded = std::move(encoded), replaceable_status]() mutable {
            self->Enqueue(std::move(encoded), replaceable_status);
          });
    }

    bool HasClient() const { return has_client_.load(); }

   private:
    void CloseAcceptor() {
      boost::system::error_code ignored;
      acceptor_.cancel(ignored);
      acceptor_.close(ignored);
    }

    void BeginAccept() {
      if (!started_) {
        return;
      }

      auto candidate = std::make_shared<tcp::socket>(io_);
      auto self = shared_from_this();
      acceptor_.async_accept(
          *candidate,
          [self, candidate](const boost::system::error_code& error) {
            self->HandleAccept(candidate, error);
          });
    }

    void HandleAccept(const std::shared_ptr<tcp::socket>& candidate,
                      const boost::system::error_code& error) {
      if (!started_) {
        return;
      }

      if (error) {
        if (error != boost::asio::error::operation_aborted) {
          LogControl("accept failed: " + error.message());
          BeginAccept();
        }
        return;
      }

      if (client_socket_) {
        RejectAdditionalClient(candidate);
        BeginAccept();
        return;
      }

      boost::system::error_code option_error;
      candidate->set_option(tcp::no_delay(true), option_error);
      if (option_error) {
        LogControl("could not enable TCP_NODELAY: " +
                   option_error.message());
      }

      client_socket_ = candidate;
      input_line_.clear();
      outgoing_.clear();
      write_in_progress_ = false;
      close_after_write_ = false;
      has_client_.store(true);

      try {
        if (on_connection_) {
          on_connection_(true);
        }
      } catch (const std::exception& exception) {
        LogControl(std::string("connection handler failed: ") +
                   exception.what());
      }

      LogControl("control client connected");
      BeginRead(candidate);
      BeginAccept();
    }

    void RejectAdditionalClient(const std::shared_ptr<tcp::socket>& socket) {
      auto response = std::make_shared<std::string>(JsonLine({
          {"v", 1},
          {"type", "error"},
          {"code", "CONTROL_CLIENT_BUSY"},
          {"message", "another control client is already connected"},
      }));

      boost::asio::async_write(
          *socket, boost::asio::buffer(*response),
          [socket, response](const boost::system::error_code&,
                             std::size_t) {
            boost::system::error_code ignored;
            socket->shutdown(tcp::socket::shutdown_both, ignored);
            socket->close(ignored);
          });
    }

    void BeginRead(const std::shared_ptr<tcp::socket>& socket) {
      if (socket != client_socket_ || close_after_write_) {
        return;
      }

      auto self = shared_from_this();
      socket->async_read_some(
          boost::asio::buffer(read_chunk_),
          [self, socket](const boost::system::error_code& error,
                         std::size_t bytes_read) {
            self->HandleRead(socket, error, bytes_read);
          });
    }

    void HandleRead(const std::shared_ptr<tcp::socket>& socket,
                    const boost::system::error_code& error,
                    std::size_t bytes_read) {
      if (socket != client_socket_) {
        return;
      }

      if (error) {
        if (error != boost::asio::error::operation_aborted &&
            error != boost::asio::error::eof) {
          LogControl("read failed: " + error.message());
        }
        DisconnectClient("connection closed");
        return;
      }

      const std::size_t maximum =
          std::max<std::size_t>(1, config_.max_control_line_bytes);
      for (std::size_t index = 0; index < bytes_read; ++index) {
        const char character = read_chunk_[index];
        if (character == '\n') {
          if (!input_line_.empty() && input_line_.back() == '\r') {
            input_line_.pop_back();
          }
          if (!input_line_.empty()) {
            HandleLine(std::move(input_line_));
          }
          input_line_.clear();
          if (close_after_write_) {
            return;
          }
          continue;
        }

        if (input_line_.size() >= maximum) {
          input_line_.clear();
          SendProtocolError("CONTROL_LINE_TOO_LARGE",
                            "control JSON line exceeds " +
                                std::to_string(maximum) + " bytes");
          close_after_write_ = true;
          if (!write_in_progress_) {
            BeginWrite();
          }
          return;
        }
        input_line_.push_back(character);
      }

      BeginRead(socket);
    }

    void HandleLine(std::string line) {
      try {
        auto message = nlohmann::json::parse(line);
        if (on_message_) {
          on_message_(message);
        }
      } catch (const nlohmann::json::parse_error& exception) {
        LogControl(std::string("invalid JSON: ") + exception.what());
        SendProtocolError("INVALID_JSON", "control message is not valid JSON");
      } catch (const std::exception& exception) {
        LogControl(std::string("message handler failed: ") +
                   exception.what());
        SendProtocolError("MESSAGE_HANDLER_ERROR",
                          "control message could not be processed");
      }
    }

    void SendProtocolError(const std::string& code,
                           const std::string& message) {
      Enqueue(JsonLine({
                  {"v", 1},
                  {"type", "error"},
                  {"code", code},
                  {"message", message},
              }),
              false);
    }

    void Enqueue(std::string encoded, bool replaceable_status) {
      if (!client_socket_ || !client_socket_->is_open()) {
        return;
      }

      // Status messages describe a snapshot. If an older, unsent status is
      // queued, replacing it prevents a slow client from replaying stale state.
      if (replaceable_status) {
        const std::size_t first_replaceable = write_in_progress_ ? 1 : 0;
        for (std::size_t index = outgoing_.size(); index > first_replaceable;
             --index) {
          auto& pending = outgoing_[index - 1];
          if (pending.replaceable_status) {
            pending.data =
                std::make_shared<std::string>(std::move(encoded));
            return;
          }
        }
      }

      if (outgoing_.size() >= kMaxOutgoingMessages) {
        const std::size_t first_removable = write_in_progress_ ? 1 : 0;
        auto removable = outgoing_.end();
        for (auto iterator = outgoing_.begin() +
                             static_cast<std::ptrdiff_t>(first_removable);
             iterator != outgoing_.end(); ++iterator) {
          if (iterator->replaceable_status) {
            removable = iterator;
            break;
          }
        }

        if (removable != outgoing_.end()) {
          outgoing_.erase(removable);
        } else if (replaceable_status) {
          return;
        } else {
          LogControl("disconnecting slow client: outgoing queue is full");
          DisconnectClient("outgoing queue full");
          return;
        }
      }

      outgoing_.push_back(
          {std::make_shared<std::string>(std::move(encoded)),
           replaceable_status});
      if (!write_in_progress_) {
        BeginWrite();
      }
    }

    void BeginWrite() {
      if (!client_socket_ || outgoing_.empty()) {
        write_in_progress_ = false;
        if (close_after_write_) {
          DisconnectClient("protocol violation");
        }
        return;
      }

      write_in_progress_ = true;
      auto socket = client_socket_;
      auto payload = outgoing_.front().data;
      auto self = shared_from_this();
      boost::asio::async_write(
          *socket, boost::asio::buffer(*payload),
          [self, socket, payload](const boost::system::error_code& error,
                                  std::size_t) {
            self->HandleWrite(socket, error);
          });
    }

    void HandleWrite(const std::shared_ptr<tcp::socket>& socket,
                     const boost::system::error_code& error) {
      if (socket != client_socket_) {
        return;
      }

      if (error) {
        if (error != boost::asio::error::operation_aborted) {
          LogControl("write failed: " + error.message());
        }
        DisconnectClient("write failed");
        return;
      }

      if (!outgoing_.empty()) {
        outgoing_.pop_front();
      }
      write_in_progress_ = false;
      BeginWrite();
    }

    void DisconnectClient(const std::string& reason) {
      if (!client_socket_) {
        has_client_.store(false);
        return;
      }

      auto socket = std::move(client_socket_);
      boost::system::error_code ignored;
      socket->cancel(ignored);
      socket->shutdown(tcp::socket::shutdown_both, ignored);
      socket->close(ignored);

      input_line_.clear();
      outgoing_.clear();
      write_in_progress_ = false;
      close_after_write_ = false;
      has_client_.store(false);

      try {
        if (on_connection_) {
          on_connection_(false);
        }
      } catch (const std::exception& exception) {
        LogControl(std::string("disconnection handler failed: ") +
                   exception.what());
      }

      LogControl("control client disconnected: " + reason);
    }

    boost::asio::io_context& io_;
    NetworkConfig config_;
    MessageHandler on_message_;
    ConnectionHandler on_connection_;
    tcp::acceptor acceptor_;
    std::shared_ptr<tcp::socket> client_socket_;
    std::array<char, kReadChunkBytes> read_chunk_{};
    std::string input_line_;
    std::deque<OutgoingMessage> outgoing_;
    bool started_ = false;
    bool write_in_progress_ = false;
    bool close_after_write_ = false;
    std::atomic<bool> has_client_{false};
  };

 public:
  Impl(boost::asio::io_context& io,
       const NetworkConfig& config,
       MessageHandler on_message,
       ConnectionHandler on_connection)
      : state_(std::make_shared<State>(io, config, std::move(on_message),
                                       std::move(on_connection))) {}

  ~Impl() { state_->Stop(); }

  void Start() { state_->Start(); }
  void Stop() { state_->Stop(); }
  void Send(nlohmann::json message, bool replaceable_status) {
    state_->Send(std::move(message), replaceable_status);
  }
  bool HasClient() const { return state_->HasClient(); }

 private:
  std::shared_ptr<State> state_;
};

ControlServer::ControlServer(boost::asio::io_context& io,
                             const NetworkConfig& config,
                             MessageHandler on_message,
                             ConnectionHandler on_connection)
    : impl_(std::make_unique<Impl>(io, config, std::move(on_message),
                                   std::move(on_connection))) {}

ControlServer::~ControlServer() = default;

void ControlServer::Start() { impl_->Start(); }

void ControlServer::Stop() { impl_->Stop(); }

void ControlServer::Send(nlohmann::json message, bool replaceable_status) {
  impl_->Send(std::move(message), replaceable_status);
}

bool ControlServer::HasClient() const { return impl_->HasClient(); }

}  // namespace tessera
