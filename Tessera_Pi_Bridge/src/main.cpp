#include <csignal>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>

#include <boost/asio.hpp>

#include "tessera/application.hpp"
#include "tessera/config.hpp"
#include "tessera/hub75_renderer.hpp"
#include "tessera/shared_state.hpp"

namespace {

struct Arguments {
  std::string config_path = "config/tessera.json";
  bool check_config = false;
};

void PrintUsage(const char* program) {
  std::cout << "Usage: " << program << " [--config PATH] [--check-config]\n";
}

Arguments ParseArguments(int argc, char** argv) {
  Arguments result;
  for (int i = 1; i < argc; ++i) {
    const std::string argument = argv[i];
    if (argument == "--config" && i + 1 < argc) {
      result.config_path = argv[++i];
    } else if (argument == "--check-config") {
      result.check_config = true;
    } else if (argument == "--help" || argument == "-h") {
      PrintUsage(argv[0]);
      std::exit(0);
    } else {
      throw std::runtime_error("Unknown argument: " + argument);
    }
  }
  return result;
}

}  // namespace

int main(int argc, char** argv) {
  try {
    const Arguments arguments = ParseArguments(argc, argv);
    const tessera::AppConfig config = tessera::LoadConfig(arguments.config_path);
    if (arguments.check_config) {
      std::cout << "Configuration is valid: " << arguments.config_path << std::endl;
      return 0;
    }

    tessera::SharedState state(config);
    tessera::Hub75Renderer renderer(config, state);

    // The matrix must be initialized before application threads are started.
    if (!renderer.Initialize()) {
      std::cerr << "Unable to initialize HUB75 output" << std::endl;
      return 2;
    }

    boost::asio::io_context io;
    tessera::Application application(io, config, state);
    boost::asio::signal_set signals(io, SIGINT, SIGTERM);
    signals.async_wait([&](const boost::system::error_code&, int signal) {
      std::cout << "Stopping on signal " << signal << std::endl;
      application.Stop();
    });

    renderer.Start();
    application.Start();
    io.run();
    application.Stop();
    renderer.Stop();
    return 0;
  } catch (const std::exception& error) {
    std::cerr << "Fatal error: " << error.what() << std::endl;
    return 1;
  }
}
