cmake_minimum_required(VERSION 3.16)

project(tabletop_os_pi_bridge VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(NOT CMAKE_CONFIGURATION_TYPES AND NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(TABLETOP_OS_ENABLE_HUB75 "Build with Raspberry Pi HUB75 hardware output" ON)
set(RGB_MATRIX_ROOT "" CACHE PATH "Path to the rpi-rgb-led-matrix checkout")

find_package(Threads REQUIRED)
find_package(Boost 1.67 REQUIRED)
find_package(nlohmann_json 3.7 QUIET)

if(NOT nlohmann_json_FOUND)
  find_path(NLOHMANN_JSON_INCLUDE_DIR nlohmann/json.hpp REQUIRED)
endif()

add_executable(tabletop-os-bridge
  src/application.cpp
  src/arduino_link.cpp
  src/config.cpp
  src/control_server.cpp
  src/cursor_receiver.cpp
  src/main.cpp
  src/shared_state.cpp
)

target_include_directories(tabletop-os-bridge PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${Boost_INCLUDE_DIRS}
)

target_compile_definitions(tabletop-os-bridge PRIVATE
  BOOST_ERROR_CODE_HEADER_ONLY
  BOOST_SYSTEM_NO_DEPRECATED
)

if(nlohmann_json_FOUND)
  target_link_libraries(tabletop-os-bridge PRIVATE nlohmann_json::nlohmann_json)
else()
  target_include_directories(tabletop-os-bridge PRIVATE ${NLOHMANN_JSON_INCLUDE_DIR})
endif()

if(TABLETOP_OS_ENABLE_HUB75)
  find_path(RGB_MATRIX_INCLUDE_DIR
    NAMES led-matrix.h
    HINTS ${RGB_MATRIX_ROOT}/include $ENV{RGB_MATRIX_ROOT}/include
    REQUIRED
  )
  find_library(RGB_MATRIX_LIBRARY
    NAMES rgbmatrix
    HINTS ${RGB_MATRIX_ROOT}/lib $ENV{RGB_MATRIX_ROOT}/lib
    REQUIRED
  )
  target_sources(tabletop-os-bridge PRIVATE src/hub75_renderer.cpp)
  target_include_directories(tabletop-os-bridge PRIVATE ${RGB_MATRIX_INCLUDE_DIR})
  target_compile_definitions(tabletop-os-bridge PRIVATE TABLETOP_OS_ENABLE_HUB75=1)
  target_link_libraries(tabletop-os-bridge PRIVATE
    ${RGB_MATRIX_LIBRARY}
    Threads::Threads
    rt
    m
  )
else()
  target_sources(tabletop-os-bridge PRIVATE src/hub75_renderer_stub.cpp)
  target_link_libraries(tabletop-os-bridge PRIVATE Threads::Threads)
endif()

if(MSVC)
  target_compile_options(tabletop-os-bridge PRIVATE /W4 /permissive-)
  target_compile_definitions(tabletop-os-bridge PRIVATE _WIN32_WINNT=0x0A00)
elseif(CMAKE_CXX_COMPILER_ID MATCHES "GNU|Clang")
  target_compile_options(tabletop-os-bridge PRIVATE -Wall -Wextra -Wpedantic)
endif()

install(TARGETS tabletop-os-bridge RUNTIME DESTINATION bin)
install(FILES config/tabletop-os.json DESTINATION /etc/tabletop-os)
install(FILES systemd/tabletop-os-bridge.service DESTINATION /etc/systemd/system)
