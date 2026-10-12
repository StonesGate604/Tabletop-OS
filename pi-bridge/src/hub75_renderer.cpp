#include "tabletop_os/hub75_renderer.hpp"

#if defined(TABLETOP_OS_ENABLE_HUB75) && TABLETOP_OS_ENABLE_HUB75

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "led-matrix.h"

namespace tabletop_os {
namespace {

using rgb_matrix::Color;

std::uint8_t ScaleChannel(std::uint8_t value, double scale) {
  const double scaled = static_cast<double>(value) *
                        std::clamp(scale, 0.0, 1.0);
  return static_cast<std::uint8_t>(std::lround(scaled));
}

bool IsGrabGesture(std::string gesture) {
  std::transform(gesture.begin(), gesture.end(), gesture.begin(),
                 [](unsigned char character) {
                   if (character >= 'A' && character <= 'Z') {
                     return static_cast<char>(character - 'A' + 'a');
                   }
                   return static_cast<char>(character);
                 });
  return gesture == "grab" || gesture == "grabbing" ||
         gesture == "closed" || gesture == "fist" ||
         gesture == "pinch";
}

}  // namespace

class Hub75Renderer::Impl {
 public:
  Impl(const AppConfig& config, SharedState& state)
      : config_(config), state_(state) {}

  ~Impl() {
    Stop();
    if (matrix_) {
      matrix_->Clear();
    }
  }

  bool Initialize() {
    if (initialized_) {
      return true;
    }

    width_ = std::max(1, config_.display.expected_width);
    height_ = std::max(1, config_.display.expected_height);

    if (!config_.display.enabled) {
      initialized_ = true;
      std::cerr << "HUB75 output disabled; renderer is idle (" << width_ << 'x'
                << height_ << ").\n";
      return true;
    }

    rgb_matrix::RGBMatrix::Options matrix_options;
    matrix_options.hardware_mapping =
        config_.display.hardware_mapping.empty()
            ? "regular"
            : config_.display.hardware_mapping.c_str();
    matrix_options.rows = config_.display.rows;
    matrix_options.cols = config_.display.cols;
    matrix_options.chain_length = config_.display.chain_length;
    matrix_options.parallel = config_.display.parallel;
    matrix_options.brightness =
        std::clamp(config_.display.brightness, 1, 100);
    matrix_options.pwm_bits = std::clamp(config_.display.pwm_bits, 1, 11);
    matrix_options.disable_hardware_pulsing =
        config_.display.disable_hardware_pulsing;
    matrix_options.pixel_mapper_config =
        config_.display.pixel_mapper.empty()
            ? nullptr
            : config_.display.pixel_mapper.c_str();

    rgb_matrix::RuntimeOptions runtime_options;
    runtime_options.gpio_slowdown =
        std::max(0, config_.display.gpio_slowdown);
    runtime_options.daemon = 0;
    runtime_options.drop_privileges =
        config_.display.drop_privileges ? 1 : 0;
    if (config_.display.drop_privileges) {
      runtime_options.drop_priv_user = config_.display.drop_user.c_str();
      runtime_options.drop_priv_group = config_.display.drop_group.c_str();
    }

    matrix_.reset(rgb_matrix::RGBMatrix::CreateFromOptions(
        matrix_options, runtime_options));
    if (!matrix_) {
      std::cerr << "Failed to initialize the HUB75 matrix.\n";
      return false;
    }

    width_ = matrix_->width();
    height_ = matrix_->height();
    if (width_ != config_.display.expected_width ||
        height_ != config_.display.expected_height) {
      std::cerr << "Unexpected HUB75 logical canvas: got " << width_ << 'x'
                << height_ << ", expected "
                << config_.display.expected_width << 'x'
                << config_.display.expected_height
                << ". Check chain and pixel-mapper settings.\n";
      matrix_->Clear();
      matrix_.reset();
      back_buffer_ = nullptr;
      return false;
    }

    back_buffer_ = matrix_->CreateFrameCanvas();
    if (back_buffer_ == nullptr) {
      std::cerr << "Failed to allocate the HUB75 back buffer.\n";
      matrix_->Clear();
      matrix_.reset();
      return false;
    }

    frame_.assign(static_cast<std::size_t>(width_) *
                      static_cast<std::size_t>(height_),
                  Color(0, 0, 0));
    initialized_ = true;
    std::cerr << "HUB75 renderer ready: " << width_ << 'x' << height_
              << " at " << std::max(1, config_.display.render_fps)
              << " FPS.\n";
    return true;
  }

  void Start() {
    if (!initialized_ || !config_.display.enabled || !matrix_) {
      return;
    }
    bool expected = false;
    if (!running_.compare_exchange_strong(expected, true)) {
      return;
    }
    render_thread_ = std::thread([this] { RenderLoop(); });
  }

  void Stop() {
    running_.store(false);
    if (render_thread_.joinable()) {
      render_thread_.join();
    }

    if (matrix_ && back_buffer_) {
      std::fill(frame_.begin(), frame_.end(), Color(0, 0, 0));
      back_buffer_->SetPixels(0, 0, width_, height_, frame_.data());
      back_buffer_ = matrix_->SwapOnVSync(back_buffer_);
    }
  }

  int Width() const { return width_; }
  int Height() const { return height_; }

 private:
  bool MapToPixel(double x_mm, double y_mm, int* x_px, int* y_px) const {
    if (x_px == nullptr || y_px == nullptr || !std::isfinite(x_mm) ||
        !std::isfinite(y_mm)) {
      return false;
    }

    const double x_span = config_.board.max_x_mm - config_.board.min_x_mm;
    const double y_span = config_.board.max_y_mm - config_.board.min_y_mm;
    if (x_span <= 0.0 || y_span <= 0.0 || width_ <= 0 || height_ <= 0) {
      return false;
    }

    double x_normalized =
        (x_mm - config_.board.min_x_mm) / x_span;
    double y_normalized =
        (y_mm - config_.board.min_y_mm) / y_span;
    x_normalized = std::clamp(x_normalized, 0.0, 1.0);
    y_normalized = std::clamp(y_normalized, 0.0, 1.0);

    if (config_.board.flip_x) {
      x_normalized = 1.0 - x_normalized;
    }
    if (config_.board.flip_y) {
      y_normalized = 1.0 - y_normalized;
    }

    *x_px = static_cast<int>(
        std::lround(x_normalized * static_cast<double>(width_ - 1)));
    *y_px = static_cast<int>(
        std::lround(y_normalized * static_cast<double>(height_ - 1)));
    return true;
  }

  double TrailAlpha(const TrailPoint& point, SteadyTime now) const {
    if (config_.trail.lifetime_ms <= 0 ||
        point.recorded_at == SteadyTime{}) {
      return 0.0;
    }
    const double age_ms = std::max(
        0.0,
        std::chrono::duration<double, std::milli>(now - point.recorded_at)
            .count());
    return std::clamp(
        1.0 - age_ms / static_cast<double>(config_.trail.lifetime_ms),
        0.0, 1.0);
  }

  bool CursorIsFresh(const CursorState& cursor, SteadyTime now) const {
    if (!cursor.visible || cursor.received_at == SteadyTime{} ||
        config_.cursor.timeout_ms <= 0) {
      return false;
    }
    const auto age = std::chrono::duration_cast<std::chrono::milliseconds>(
        now - cursor.received_at);
    return age.count() <= config_.cursor.timeout_ms;
  }

  void PutPixel(int x, int y, std::uint8_t green, std::uint8_t blue,
                bool overwrite = false) {
    if (x < 0 || y < 0 || x >= width_ || y >= height_) {
      return;
    }
    Color& pixel = frame_[static_cast<std::size_t>(y) *
                              static_cast<std::size_t>(width_) +
                          static_cast<std::size_t>(x)];
    // Red remains deliberately disabled while the panel's red channel is
    // under investigation.
    pixel.r = 0;
    if (overwrite) {
      pixel.g = green;
      pixel.b = blue;
    } else {
      pixel.g = std::max(pixel.g, green);
      pixel.b = std::max(pixel.b, blue);
    }
  }

  void DrawTrailLine(int x0, int y0, double alpha0, int x1, int y1,
                     double alpha1) {
    const int delta_x = x1 - x0;
    const int delta_y = y1 - y0;
    const int steps = std::max(std::abs(delta_x), std::abs(delta_y));
    if (steps == 0) {
      PutPixel(x0, y0,
               ScaleChannel(config_.trail.green, alpha1),
               ScaleChannel(config_.trail.blue, alpha1));
      return;
    }

    for (int step = 0; step <= steps; ++step) {
      const double amount =
          static_cast<double>(step) / static_cast<double>(steps);
      const int x = static_cast<int>(std::lround(
          static_cast<double>(x0) + static_cast<double>(delta_x) * amount));
      const int y = static_cast<int>(std::lround(
          static_cast<double>(y0) + static_cast<double>(delta_y) * amount));
      const double alpha = alpha0 + (alpha1 - alpha0) * amount;
      PutPixel(x, y,
               ScaleChannel(config_.trail.green, alpha),
               ScaleChannel(config_.trail.blue, alpha));
    }
  }

  void DrawTrail(const std::deque<TrailPoint>& trail, SteadyTime now) {
    bool have_previous = false;
    int previous_x = 0;
    int previous_y = 0;
    double previous_alpha = 0.0;

    for (const TrailPoint& point : trail) {
      int x = 0;
      int y = 0;
      if (!MapToPixel(point.x_mm, point.y_mm, &x, &y)) {
        have_previous = false;
        continue;
      }

      const double alpha = TrailAlpha(point, now);
      if (alpha <= 0.0) {
        have_previous = false;
        continue;
      }

      if (have_previous) {
        DrawTrailLine(previous_x, previous_y, previous_alpha, x, y, alpha);
      } else {
        PutPixel(x, y, ScaleChannel(config_.trail.green, alpha),
                 ScaleChannel(config_.trail.blue, alpha));
      }
      previous_x = x;
      previous_y = y;
      previous_alpha = alpha;
      have_previous = true;
    }
  }

  void DrawCircle(int center_x, int center_y, int radius,
                  std::uint8_t green, std::uint8_t blue) {
    if (radius <= 0) {
      PutPixel(center_x, center_y, green, blue, true);
      return;
    }

    int x = radius;
    int y = 0;
    int error = 1 - radius;
    while (x >= y) {
      PutPixel(center_x + x, center_y + y, green, blue, true);
      PutPixel(center_x + y, center_y + x, green, blue, true);
      PutPixel(center_x - y, center_y + x, green, blue, true);
      PutPixel(center_x - x, center_y + y, green, blue, true);
      PutPixel(center_x - x, center_y - y, green, blue, true);
      PutPixel(center_x - y, center_y - x, green, blue, true);
      PutPixel(center_x + y, center_y - x, green, blue, true);
      PutPixel(center_x + x, center_y - y, green, blue, true);
      ++y;
      if (error < 0) {
        error += 2 * y + 1;
      } else {
        --x;
        error += 2 * (y - x) + 1;
      }
    }
  }

  void DrawCursor(const CursorState& cursor, SteadyTime now) {
    if (!CursorIsFresh(cursor, now)) {
      return;
    }

    int x = 0;
    int y = 0;
    if (!MapToPixel(cursor.x_mm, cursor.y_mm, &x, &y)) {
      return;
    }

    const int radius = std::max(1, config_.cursor.radius_px);
    const std::uint8_t green = config_.cursor.green;
    const std::uint8_t blue = config_.cursor.blue;
    DrawCircle(x, y, radius, green, blue);

    if (IsGrabGesture(cursor.gesture)) {
      // A grab is a double ring with a solid center. It remains recognizable
      // even when the ring overlaps the blue motion trail.
      DrawCircle(x, y, radius + 2,
                 ScaleChannel(green, 0.70), ScaleChannel(blue, 0.70));
      for (int offset_y = -1; offset_y <= 1; ++offset_y) {
        for (int offset_x = -1; offset_x <= 1; ++offset_x) {
          PutPixel(x + offset_x, y + offset_y, green, blue, true);
        }
      }
    }
  }

  void Render(const StateSnapshot& snapshot, SteadyTime now) {
    std::fill(frame_.begin(), frame_.end(), Color(0, 0, 0));
    DrawTrail(snapshot.trail, now);
    DrawCursor(snapshot.cursor, now);
    back_buffer_->SetPixels(0, 0, width_, height_, frame_.data());
    back_buffer_ = matrix_->SwapOnVSync(back_buffer_);
  }

  void RenderLoop() {
    using Clock = std::chrono::steady_clock;
    const int frames_per_second = std::max(1, config_.display.render_fps);
    const auto frame_period = std::chrono::nanoseconds(
        std::chrono::nanoseconds::period::den / frames_per_second);
    auto next_frame = Clock::now();

    while (running_.load()) {
      const SteadyTime now = Clock::now();
      Render(state_.Snapshot(now), now);

      next_frame += frame_period;
      const auto after_render = Clock::now();
      if (next_frame <= after_render) {
        // Do not try to replay missed frames: visual state is latest-value
        // state, so recovering by skipping ahead keeps latency bounded.
        next_frame = after_render + frame_period;
      }
      std::this_thread::sleep_until(next_frame);
    }
  }

  const AppConfig config_;
  SharedState& state_;
  std::unique_ptr<rgb_matrix::RGBMatrix> matrix_;
  rgb_matrix::FrameCanvas* back_buffer_ = nullptr;
  std::vector<Color> frame_;
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

#endif  // defined(TABLETOP_OS_ENABLE_HUB75) && TABLETOP_OS_ENABLE_HUB75
