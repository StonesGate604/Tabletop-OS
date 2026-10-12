#pragma once

#include <functional>
#include <memory>
#include <string>

#include <boost/asio.hpp>

#include "tabletop_os/config.hpp"

namespace tabletop_os {

class ArduinoLink {
 public:
  using LineHandler = std::function<void(const std::string&)>;
  using ConnectionHandler = std::function<void(bool)>;

  ArduinoLink(boost::asio::io_context& io,
              const SerialConfig& config,
              LineHandler on_line,
              ConnectionHandler on_connection);
  ~ArduinoLink();

  void Start();
  void Stop();
  void SendLine(std::string line, bool urgent = false);
  bool IsConnected() const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace tabletop_os
