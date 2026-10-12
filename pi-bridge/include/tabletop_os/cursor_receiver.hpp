#pragma once

#include <functional>
#include <memory>

#include <boost/asio.hpp>
#include <nlohmann/json.hpp>

#include "tabletop_os/config.hpp"

namespace tabletop_os {

class CursorReceiver {
 public:
  using MessageHandler = std::function<void(const nlohmann::json&)>;

  CursorReceiver(boost::asio::io_context& io,
                 const NetworkConfig& config,
                 MessageHandler on_message);
  ~CursorReceiver();

  void Start();
  void Stop();

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace tabletop_os
