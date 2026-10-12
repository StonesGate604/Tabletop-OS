#include "tessera/arduino_link.hpp"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstddef>
#include <cstdint>
#include <deque>
#include <istream>
#include <memory>
#include <string>
#include <utility>

#include <boost/asio/bind_executor.hpp>
#include <boost/asio/read_until.hpp>
#include <boost/asio/write.hpp>

namespace tessera {
namespace {

constexpr std::size_t kMaximumPendingWrites = 128;
constexpr std::size_t kMaximumOutboundLineBytes = 4096;
constexpr std::size_t kMaximumInboundBufferBytes = 8192;

struct PendingWrite {
  std::string data;
  bool urgent = false;
};

}  // namespace

class ArduinoLink::Impl {
 public:
  class State : public std::enable_shared_from_this<State> {
   public:
    State(boost::asio::io_context& io,
          const SerialConfig& config,
          LineHandler on_line,
          ConnectionHandler on_connection)
        : strand_(boost::asio::make_strand(io)),
          serial_(io),
          reconnect_timer_(io),
          read_buffer_(
              std::make_shared<boost::asio::streambuf>(kMaximumInboundBufferBytes)),
          config_(config),
          on_line_(std::move(on_line)),
          on_connection_(std::move(on_connection)) {}

    void Start() {
      if (!config_.enabled || stop_requested_.load(std::memory_order_acquire)) {
        return;
      }

      auto self = shared_from_this();
      boost::asio::post(strand_, [self]() { self->StartOnStrand(); });
    }

    void Stop() {
      const bool was_requested =
          stop_requested_.exchange(true, std::memory_order_acq_rel);
      if (was_requested) return;

      auto self = shared_from_this();
      boost::asio::post(strand_, [self]() { self->StopOnStrand(); });
    }

    void SendLine(std::string line, bool urgent) {
      if (!config_.enabled || stop_requested_.load(std::memory_order_acquire)) {
        return;
      }

      const auto delimiter = line.find_first_of("\r\n");
      if (delimiter != std::string::npos) line.erase(delimiter);
      if (line.empty() || line.size() > kMaximumOutboundLineBytes) return;
      line.push_back('\n');

      auto self = shared_from_this();
      boost::asio::post(
          strand_, [self, line = std::move(line), urgent]() mutable {
            self->EnqueueWrite(std::move(line), urgent);
          });
    }

    bool IsConnected() const {
      return config_.enabled &&
             !stop_requested_.load(std::memory_order_acquire) &&
             connected_.load(std::memory_order_acquire);
    }

   private:
    void StartOnStrand() {
      if (started_ || stop_requested_.load(std::memory_order_acquire)) return;
      started_ = true;
      OpenSerial();
    }

    void StopOnStrand() {
      started_ = false;
      reconnect_scheduled_ = false;
      ++connection_generation_;

      boost::system::error_code ignored;
      reconnect_timer_.cancel(ignored);
      serial_.cancel(ignored);
      serial_.close(ignored);

      write_in_progress_ = false;
      pending_writes_.clear();
      NotifyConnection(false);
    }

    void OpenSerial() {
      if (!started_ || stop_requested_.load(std::memory_order_acquire)) return;

      reconnect_scheduled_ = false;
      boost::system::error_code error;
      if (serial_.is_open()) serial_.close(error);

      error.clear();
      serial_.open(config_.device, error);
      if (!error) {
        serial_.set_option(
            boost::asio::serial_port_base::baud_rate(config_.baud), error);
      }
      if (!error) {
        serial_.set_option(
            boost::asio::serial_port_base::character_size(8), error);
      }
      if (!error) {
        serial_.set_option(boost::asio::serial_port_base::parity(
                               boost::asio::serial_port_base::parity::none),
                           error);
      }
      if (!error) {
        serial_.set_option(boost::asio::serial_port_base::stop_bits(
                               boost::asio::serial_port_base::stop_bits::one),
                           error);
      }
      if (!error) {
        serial_.set_option(
            boost::asio::serial_port_base::flow_control(
                boost::asio::serial_port_base::flow_control::none),
            error);
      }

      if (error) {
        boost::system::error_code ignored;
        serial_.close(ignored);
        NotifyConnection(false);
        ScheduleReconnect();
        return;
      }

      ++connection_generation_;
      read_buffer_ =
          std::make_shared<boost::asio::streambuf>(kMaximumInboundBufferBytes);
      write_in_progress_ = false;
      NotifyConnection(true);

      const std::uint64_t generation = connection_generation_;
      BeginRead(generation);
      BeginWrite(generation);
    }

    void ScheduleReconnect() {
      if (reconnect_scheduled_ || !started_ ||
          stop_requested_.load(std::memory_order_acquire)) {
        return;
      }

      reconnect_scheduled_ = true;
      reconnect_timer_.expires_after(
          std::chrono::milliseconds(std::max(config_.reconnect_ms, 1)));

      auto self = shared_from_this();
      reconnect_timer_.async_wait(boost::asio::bind_executor(
          strand_, [self](const boost::system::error_code& error) {
            self->reconnect_scheduled_ = false;
            if (error == boost::asio::error::operation_aborted ||
                self->stop_requested_.load(std::memory_order_acquire) ||
                !self->started_) {
              return;
            }
            self->OpenSerial();
          }));
    }

    void BeginRead(std::uint64_t generation) {
      if (!IsCurrentConnection(generation)) return;

      const auto read_buffer = read_buffer_;
      auto self = shared_from_this();
      boost::asio::async_read_until(
          serial_, *read_buffer, '\n',
          boost::asio::bind_executor(
              strand_, [self, read_buffer, generation](
                           const boost::system::error_code& error,
                           std::size_t /*bytes_transferred*/) {
                if (!self->IsCurrentConnection(generation)) return;
                if (error) {
                  self->HandleDisconnect(generation);
                  return;
                }

                std::istream input(read_buffer.get());
                std::string line;
                std::getline(input, line);
                if (!line.empty() && line.back() == '\r') line.pop_back();
                self->DeliverLine(line);
                self->BeginRead(generation);
              }));
    }

    void EnqueueWrite(std::string line, bool urgent) {
      // Commands are never retained across a disconnected period. In particular,
      // an old motion command must not execute merely because the USB cable was
      // reconnected later.
      if (!started_ || stop_requested_.load(std::memory_order_acquire) ||
          !connected_.load(std::memory_order_acquire)) {
        return;
      }

      if (pending_writes_.size() >= kMaximumPendingWrites) {
        if (!urgent) return;
        pending_writes_.pop_back();
      }

      PendingWrite write{std::move(line), urgent};
      if (urgent) {
        pending_writes_.push_front(std::move(write));
      } else {
        pending_writes_.push_back(std::move(write));
      }

      BeginWrite(connection_generation_);
    }

    void BeginWrite(std::uint64_t generation) {
      if (!IsCurrentConnection(generation) || write_in_progress_ ||
          pending_writes_.empty()) {
        return;
      }

      auto write =
          std::make_shared<PendingWrite>(std::move(pending_writes_.front()));
      pending_writes_.pop_front();
      write_in_progress_ = true;

      auto self = shared_from_this();
      boost::asio::async_write(
          serial_, boost::asio::buffer(write->data),
          boost::asio::bind_executor(
              strand_, [self, write, generation](
                           const boost::system::error_code& error,
                           std::size_t /*bytes_transferred*/) {
                if (!self->IsCurrentConnection(generation)) return;
                self->write_in_progress_ = false;
                if (error) {
                  self->HandleDisconnect(generation);
                  return;
                }
                self->BeginWrite(generation);
              }));
    }

    bool IsCurrentConnection(std::uint64_t generation) const {
      return started_ && !stop_requested_.load(std::memory_order_acquire) &&
             connected_.load(std::memory_order_acquire) &&
             generation == connection_generation_;
    }

    void HandleDisconnect(std::uint64_t generation) {
      if (generation != connection_generation_) return;

      ++connection_generation_;
      boost::system::error_code ignored;
      serial_.cancel(ignored);
      serial_.close(ignored);

      write_in_progress_ = false;
      pending_writes_.clear();
      NotifyConnection(false);
      ScheduleReconnect();
    }

    void NotifyConnection(bool connected) {
      const bool previous =
          connected_.exchange(connected, std::memory_order_acq_rel);
      if (previous == connected || !on_connection_) return;

      try {
        on_connection_(connected);
      } catch (...) {
        // A consumer callback must not terminate the serial event loop.
      }
    }

    void DeliverLine(const std::string& line) {
      if (!on_line_) return;
      try {
        on_line_(line);
      } catch (...) {
        // A malformed consumer callback must not stop subsequent reads.
      }
    }

    boost::asio::strand<boost::asio::io_context::executor_type> strand_;
    boost::asio::serial_port serial_;
    boost::asio::steady_timer reconnect_timer_;
    std::shared_ptr<boost::asio::streambuf> read_buffer_;
    const SerialConfig config_;
    LineHandler on_line_;
    ConnectionHandler on_connection_;

    std::deque<PendingWrite> pending_writes_;
    std::atomic<bool> connected_{false};
    std::atomic<bool> stop_requested_{false};
    bool started_ = false;
    bool reconnect_scheduled_ = false;
    bool write_in_progress_ = false;
    std::uint64_t connection_generation_ = 0;
  };

  Impl(boost::asio::io_context& io,
       const SerialConfig& config,
       LineHandler on_line,
       ConnectionHandler on_connection)
      : state_(std::make_shared<State>(io,
                                       config,
                                       std::move(on_line),
                                       std::move(on_connection))) {}

  ~Impl() { state_->Stop(); }

  void Start() { state_->Start(); }
  void Stop() { state_->Stop(); }
  void SendLine(std::string line, bool urgent) {
    state_->SendLine(std::move(line), urgent);
  }
  bool IsConnected() const { return state_->IsConnected(); }

 private:
  std::shared_ptr<State> state_;
};

ArduinoLink::ArduinoLink(boost::asio::io_context& io,
                         const SerialConfig& config,
                         LineHandler on_line,
                         ConnectionHandler on_connection)
    : impl_(std::make_unique<Impl>(io,
                                   config,
                                   std::move(on_line),
                                   std::move(on_connection))) {}

ArduinoLink::~ArduinoLink() = default;

void ArduinoLink::Start() { impl_->Start(); }

void ArduinoLink::Stop() { impl_->Stop(); }

void ArduinoLink::SendLine(std::string line, bool urgent) {
  impl_->SendLine(std::move(line), urgent);
}

bool ArduinoLink::IsConnected() const { return impl_->IsConnected(); }

}  // namespace tessera
