#pragma once

#include <functional>
#include <memory>

#include <boost/asio.hpp>
#include <nlohmann/json.hpp>

#include "tabletop_os/config.hpp"

namespace tabletop_os {

class ControlServer {
 public:
  using MessageHandler = std::function<void(const nlohmann::json&)>;
  using ConnectionHandler = std::function<void(bool)>;

  ControlServer(boost::asio::io_context& io,
                const NetworkConfig& config,
                MessageHandler on_message,
                ConnectionHandler on_connection);
  ~ControlServer();

  void Start();
  void Stop();
  void Send(nlohmann::json message, bool replaceable_status = false);
  bool HasClient() const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace tabletop_os
