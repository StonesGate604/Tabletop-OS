#include <csignal>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <string>

#include <boost/asio.hpp>

#include "tabletop_os/application.hpp"
#include "tabletop_os/config.hpp"
#include "tabletop_os/hub75_renderer.hpp"
#include "tabletop_os/shared_state.hpp"

namespace {

struct Arguments {
  // 默认读取当前工作目录下的配置；--config 可以指定另一份配置文件。
  std::string config_path = "config/tabletop-os.json";
  // 为 true 时只检查配置，检查完成就退出。
  bool check_config = false;
};

void PrintUsage(const char* program) {
  std::cout << "Usage: " << program << " [--config PATH] [--check-config]\n";
}

Arguments ParseArguments(int argc, char** argv) {
  Arguments result;
  // argv[0] 是程序名，后面的字符串才是用户传入的选项。
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
  // 程序启动后从这里执行。argc 是命令行参数数量，argv 保存各个参数。
  // 整体顺序：读取配置 → 准备显示 → 启动通信和显示 → 等待事件 → 退出清理。
  try {
    // 1. 解析启动选项。例如：
    // tabletop-os-bridge --config /etc/tabletop-os/tabletop-os.json
    const Arguments arguments = ParseArguments(argc, argv);

    // 2. 读取并校验 JSON 配置，其中包含网络端口、串口、灯板和运动限制。
    // 文件不存在或配置不合法时会抛出异常，交给下面的 catch 处理。
    const tabletop_os::AppConfig config = tabletop_os::LoadConfig(arguments.config_path);
    if (arguments.check_config) {
      // 带 --check-config 启动时，到这里就结束，不启动通信或灯板。
      std::cout << "Configuration is valid: " << arguments.config_path << std::endl;
      return 0;
    }

    // 3. 建立共享状态，保存最新的光标位置、Arduino 状态和运动轨迹。
    // 通信模块更新它，显示模块读取它；内部用锁保护这两边的访问。
    tabletop_os::SharedState state(config);

    // 建立显示模块，让它能读取同一份共享状态。此时还没有开始画图。
    tabletop_os::Hub75Renderer renderer(config, state);

    // 4. 按配置准备 HUB75 灯板和画面缓冲；初始化必须放在线程启动之前。
    // 初始化失败就返回 2，让调用者知道程序没有成功启动。
    if (!renderer.Initialize()) {
      std::cerr << "Unable to initialize HUB75 output" << std::endl;
      return 2;
    }

    // 5. 建立事件调度器。它负责等待网络数据、串口数据、定时器和退出信号，
    // 事件到来后调用相应的处理函数。真正开始处理事件的是后面的 io.run()。
    boost::asio::io_context io;

    // 建立通信与协调模块，传入调度器、配置和共享状态。
    // 具体的 JSON 命令处理、Arduino 握手和心跳检查在 application.cpp 中。
    tabletop_os::Application application(io, config, state);

    // 6. 登记退出信号：SIGINT 通常来自 Ctrl+C，SIGTERM 通常来自系统停止请求。
    boost::asio::signal_set signals(io, SIGINT, SIGTERM);

    // 这里登记一个回调：收到信号后才执行花括号中的代码。
    // [&] 表示回调通过引用访问外面的变量，例如 application。
    signals.async_wait([&](const boost::system::error_code&, int signal) {
      std::cout << "Stopping on signal " << signal << std::endl;

      // 发起退出流程：停止接收网络消息；串口连接时先向 Arduino 请求 STOP，
      // 等停止回报或退出宽限时间结束后再关闭串口。
      application.Stop();
    });

    // 7. 启动独立的显示线程（显示启用时），持续读取状态、绘图并刷新灯板。
    renderer.Start();

    // 8. 开始监听 TCP/UDP、启动 Arduino 串口连接，并安排定时维护。
    // 默认输入：TCP 7420 接收控制 JSON，UDP 7421 接收光标 JSON，
    // USB 串口接收 Arduino 的文字消息；实际端口和串口以配置文件为准。
    application.Start();

    // 9. 主事件循环：这里会一直处理事件，平时没有事件就等待。
    // 收到数据 → 调用对应处理函数 → 更新状态或发送回复 → 继续等下一次事件。
    // 定时器也属于事件，心跳超时检查就在这个循环中被触发。
    // 通常退出清理完成、没有待处理异步工作后，run() 才返回并执行下一行。
    io.run();

    // 10. 收尾。Stop() 可重复调用；这里确保通信进入停止流程，
    // 然后停止显示线程、等待它结束并清屏。
    application.Stop();
    renderer.Stop();

    // 返回 0 表示正常结束。
    return 0;
  } catch (const std::exception& error) {
    // 启动或运行中抛出的异常会到这里：打印原因，返回 1 表示异常退出。
    std::cerr << "Fatal error: " << error.what() << std::endl;
    return 1;
  }
}
