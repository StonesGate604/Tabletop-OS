#include "tabletop_os/hub75_renderer.hpp"

#if !defined(TABLETOP_OS_ENABLE_HUB75) || !TABLETOP_OS_ENABLE_HUB75

#include <algorithm>
#include <atomic>
#include <chrono>
#include <iostream>
#include <memory>
#include <thread>

namespace tabletop_os {

class Hub75Renderer::Impl {
 public:
  Impl(const AppConfig& config, SharedState& state)
      : config_(config), state_(state) {}

  ~Impl() { Stop(); }

  bool Initialize() {
    width_ = std::max(1, config_.display.expected_width);
    height_ = std::max(1, config_.display.expected_height);
    initialized_ = true;
    std::cerr << "HUB75 hardware support is not compiled in; using the "
                 "timing stub ("
              << width_ << 'x' << height_ << ", "
              << std::max(1, config_.display.render_fps) << " FPS).\n";
    return true;
  }

  void Start() {
    if (!initialized_) {
      return;
    }
    bool expected = false;
    if (!running_.compare_exchange_strong(expected, true)) {
      return;
    }
    render_thread_ = std::thread([this] { TimingLoop(); });
  }

  void Stop() {
    running_.store(false);
    if (render_thread_.joinable()) {
      render_thread_.join();
    }
  }

  int Width() const { return width_; }
  int Height() const { return height_; }

 private:
  void TimingLoop() {
    using Clock = std::chrono::steady_clock;
    const int frames_per_second = std::max(1, config_.display.render_fps);
    const auto frame_period = std::chrono::nanoseconds(
        std::chrono::nanoseconds::period::den / frames_per_second);
    auto next_frame = Clock::now();

    while (running_.load()) {
      const SteadyTime now = Clock::now();
      // Snapshotting here exercises the same cursor-expiry and state-locking
      // path as the hardware renderer without touching GPIO.
      (void)state_.Snapshot(now);

      next_frame += frame_period;
      const auto after_tick = Clock::now();
      if (next_frame <= after_tick) {
        next_frame = after_tick + frame_period;
      }
      std::this_thread::sleep_until(next_frame);
    }
  }

  const AppConfig config_;
  SharedState& state_;
  std::atomic<bool> running_{false};
  std::thread render_thread_;
  bool initialized_ = false;
  int width_ = 0;
  int height_ = 0;
};

Hub75Renderer::Hub75Renderer(const AppConfig& config, SharedState& state)
    : impl_(std::make_unique<Impl>(config, state)) {}

Hub75Renderer::~Hub75Renderer() { Stop(); }

bool Hub75Renderer::Initialize() { return impl_->Initialize(); }

void Hub75Renderer::Start() { impl_->Start(); }

void Hub75Renderer::Stop() { impl_->Stop(); }

int Hub75Renderer::Width() const { return impl_->Width(); }

int Hub75Renderer::Height() const { return impl_->Height(); }

}  // namespace tabletop_os

#endif  // !defined(TABLETOP_OS_ENABLE_HUB75) || !TABLETOP_OS_ENABLE_HUB75
