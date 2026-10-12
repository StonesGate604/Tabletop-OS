#pragma once

#include <atomic>
#include <memory>
#include <thread>

#include "tessera/config.hpp"
#include "tessera/shared_state.hpp"

namespace tessera {

class Hub75Renderer {
 public:
  Hub75Renderer(const AppConfig& config, SharedState& state);
  ~Hub75Renderer();

  bool Initialize();
  void Start();
  void Stop();
  int Width() const;
  int Height() const;

 private:
  class Impl;
  std::unique_ptr<Impl> impl_;
};

}  // namespace tessera
