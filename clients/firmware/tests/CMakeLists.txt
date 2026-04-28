cmake_minimum_required(VERSION 3.16)
project(huxley_firmware_tests C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)

# We compile with strong warnings; treat as errors so drift lands as a
# test failure, not a quiet slip.
add_compile_options(
    -Wall -Wextra -Werror
    -Wno-error=unused-function       # helper macros leave unused-by-design globals
    -Wno-unused-function
)

enable_testing()

# -- cJSON -----------------------------------------------------------
# Use ESP-IDF's bundled cJSON so the host build and the device build
# share the same version. If ESP-IDF isn't sourced, fail loudly.
if(NOT DEFINED ENV{IDF_PATH})
    message(FATAL_ERROR
        "IDF_PATH not set. Run `. ~/esp/esp-idf/export.sh` before building.")
endif()
set(CJSON_DIR "$ENV{IDF_PATH}/components/json/cJSON")
if(NOT EXISTS "${CJSON_DIR}/cJSON.c")
    message(FATAL_ERROR
        "cJSON not found at ${CJSON_DIR}. ESP-IDF layout may have changed.")
endif()

add_library(cjson STATIC ${CJSON_DIR}/cJSON.c)
target_include_directories(cjson PUBLIC ${CJSON_DIR})

# -- hux_proto (library under test) ---------------------------------
set(HUX_PROTO_DIR ${CMAKE_SOURCE_DIR}/../components/hux_proto)
add_library(hux_proto STATIC ${HUX_PROTO_DIR}/hux_proto.c)
target_include_directories(hux_proto PUBLIC ${HUX_PROTO_DIR}/include)
target_link_libraries(hux_proto PUBLIC cjson)

# -- hux_ws_frag (library under test) -------------------------------
# Pure C, no ESP-IDF deps beyond stddef/stdint. Lives inside hux_net
# but compiles standalone here.
set(HUX_NET_DIR ${CMAKE_SOURCE_DIR}/../components/hux_net)
add_library(hux_ws_frag STATIC ${HUX_NET_DIR}/hux_ws_frag.c)
target_include_directories(hux_ws_frag PUBLIC ${HUX_NET_DIR}/include)

# -- tests -----------------------------------------------------------
add_executable(test_hux_proto test_hux_proto.c)
target_include_directories(test_hux_proto PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_proto PRIVATE hux_proto)
add_test(NAME hux_proto COMMAND test_hux_proto)

add_executable(test_hux_ws_frag test_hux_ws_frag.c)
target_include_directories(test_hux_ws_frag PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_ws_frag PRIVATE hux_ws_frag)
add_test(NAME hux_ws_frag COMMAND test_hux_ws_frag)

# Convenience target: `cmake --build build --target check` runs every test.
add_custom_target(check
    COMMAND ${CMAKE_CTEST_COMMAND} --output-on-failure
    DEPENDS test_hux_proto test_hux_ws_frag
)
