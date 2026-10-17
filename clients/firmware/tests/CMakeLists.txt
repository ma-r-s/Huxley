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

# -- hux_type_probe (library under test): the data-plane router, in hux_net.
add_library(hux_type_probe STATIC ${HUX_NET_DIR}/hux_type_probe.c)
target_include_directories(hux_type_probe PUBLIC ${HUX_NET_DIR}/include)

# -- hux_wifi_retry (library under test): Wi-Fi reconnect backoff, in hux_net.
add_library(hux_wifi_retry STATIC ${HUX_NET_DIR}/hux_wifi_retry.c)
target_include_directories(hux_wifi_retry PUBLIC ${HUX_NET_DIR}/include)

# -- hux_wifi_list (library under test): which known network to join, in hux_net.
add_library(hux_wifi_list STATIC ${HUX_NET_DIR}/hux_wifi_list.c)
target_include_directories(hux_wifi_list PUBLIC ${HUX_NET_DIR}/include)

# -- hux_link (library under test): WS backoff + escalation ladder, in hux_net.
add_library(hux_link STATIC ${HUX_NET_DIR}/hux_link.c)
target_include_directories(hux_link PUBLIC ${HUX_NET_DIR}/include)

# -- hux_blackbox (library under test): the RTC ring of last warnings, in hux_log.
set(HUX_LOG_DIR ${CMAKE_SOURCE_DIR}/../components/hux_log)
add_library(hux_blackbox STATIC ${HUX_LOG_DIR}/hux_blackbox.c)
target_include_directories(hux_blackbox PUBLIC ${HUX_LOG_DIR}/include)

# -- hux_super_check (library under test): the supervisor's stall decision.
set(HUX_SUPER_DIR ${CMAKE_SOURCE_DIR}/../components/hux_super)
add_library(hux_super_check STATIC ${HUX_SUPER_DIR}/hux_super_check.c)
target_include_directories(hux_super_check PUBLIC ${HUX_SUPER_DIR}/include)

# -- hux_battery_level (library under test): cell voltage to percent.
set(HUX_BATTERY_DIR ${CMAKE_SOURCE_DIR}/../components/hux_battery)
add_library(hux_battery_level STATIC ${HUX_BATTERY_DIR}/hux_battery_level.c)
target_include_directories(hux_battery_level PUBLIC ${HUX_BATTERY_DIR}/include)
add_library(hux_battery_cutoff STATIC ${HUX_BATTERY_DIR}/hux_battery_cutoff.c)
target_include_directories(hux_battery_cutoff PUBLIC ${HUX_BATTERY_DIR}/include)

# -- hux_prompt_parse (library under test): the stored prompt files.
set(HUX_PROMPTS_DIR ${CMAKE_SOURCE_DIR}/../components/hux_prompts)
add_library(hux_prompt_parse STATIC ${HUX_PROMPTS_DIR}/hux_prompt_parse.c)
target_include_directories(hux_prompt_parse PUBLIC ${HUX_PROMPTS_DIR}/include)

# -- hux_echo (library under test): the call echo suppressor, in hux_audio.
set(HUX_AUDIO_DIR ${CMAKE_SOURCE_DIR}/../components/hux_audio)
add_library(hux_echo STATIC ${HUX_AUDIO_DIR}/hux_echo.c)
target_include_directories(hux_echo PUBLIC ${HUX_AUDIO_DIR}/include)
target_link_libraries(hux_echo PUBLIC m)

# -- hux_alert_logic (library under test): the walkie's own phone alerts.
set(HUX_ALERT_DIR ${CMAKE_SOURCE_DIR}/../components/hux_alert)
add_library(hux_alert_logic STATIC ${HUX_ALERT_DIR}/hux_alert_logic.c)
target_include_directories(hux_alert_logic PUBLIC ${HUX_ALERT_DIR}/include)

# -- hux_status (library under test): the connection sounds, in hux_app.
set(HUX_APP_DIR ${CMAKE_SOURCE_DIR}/../components/hux_app)
add_library(hux_status STATIC ${HUX_APP_DIR}/hux_status.c)
target_include_directories(hux_status PUBLIC ${HUX_APP_DIR}/include)

# -- hux_led_anim (library under test): the light under the clear back.
set(HUX_LED_DIR ${CMAKE_SOURCE_DIR}/../components/hux_led)
add_library(hux_led_anim STATIC ${HUX_LED_DIR}/hux_led_anim.c)
target_include_directories(hux_led_anim PUBLIC ${HUX_LED_DIR}/include)
target_link_libraries(hux_led_anim PUBLIC m)

# -- hux_debounce (library under test) ------------------------------
# Pure C, the talk button's debounce; lives inside hux_button.
set(HUX_BUTTON_DIR ${CMAKE_SOURCE_DIR}/../components/hux_button)
add_library(hux_debounce STATIC ${HUX_BUTTON_DIR}/hux_debounce.c)
target_include_directories(hux_debounce PUBLIC ${HUX_BUTTON_DIR}/include)

# -- tests -----------------------------------------------------------
add_executable(test_hux_proto test_hux_proto.c)
target_include_directories(test_hux_proto PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_proto PRIVATE hux_proto)
add_test(NAME hux_proto COMMAND test_hux_proto)

add_executable(test_hux_ws_frag test_hux_ws_frag.c)
target_include_directories(test_hux_ws_frag PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_ws_frag PRIVATE hux_ws_frag)
add_test(NAME hux_ws_frag COMMAND test_hux_ws_frag)

add_executable(test_hux_type_probe test_hux_type_probe.c)
target_include_directories(test_hux_type_probe PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_type_probe PRIVATE hux_type_probe)
add_test(NAME hux_type_probe COMMAND test_hux_type_probe)

add_executable(test_hux_debounce test_hux_debounce.c)
target_include_directories(test_hux_debounce PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_debounce PRIVATE hux_debounce)
add_test(NAME hux_debounce COMMAND test_hux_debounce)

add_executable(test_hux_wifi_retry test_hux_wifi_retry.c)
target_include_directories(test_hux_wifi_retry PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_wifi_retry PRIVATE hux_wifi_retry)
add_test(NAME hux_wifi_retry COMMAND test_hux_wifi_retry)

add_executable(test_hux_link test_hux_link.c)
target_include_directories(test_hux_link PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_link PRIVATE hux_link)
add_test(NAME hux_link COMMAND test_hux_link)

add_executable(test_hux_blackbox test_hux_blackbox.c)
target_include_directories(test_hux_blackbox PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_blackbox PRIVATE hux_blackbox)
add_test(NAME hux_blackbox COMMAND test_hux_blackbox)

add_executable(test_hux_super_check test_hux_super_check.c)
target_include_directories(test_hux_super_check PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_super_check PRIVATE hux_super_check)
add_test(NAME hux_super_check COMMAND test_hux_super_check)

add_executable(test_hux_battery_level test_hux_battery_level.c)
target_include_directories(test_hux_battery_level PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_battery_level PRIVATE hux_battery_level)
add_test(NAME hux_battery_level COMMAND test_hux_battery_level)

add_executable(test_hux_prompt_parse test_hux_prompt_parse.c)
target_include_directories(test_hux_prompt_parse PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_prompt_parse PRIVATE hux_prompt_parse)
target_compile_definitions(test_hux_prompt_parse PRIVATE HUX_PROMPTS_FILES="${HUX_PROMPTS_DIR}/prompts")
add_test(NAME hux_prompt_parse COMMAND test_hux_prompt_parse)

add_executable(test_hux_battery_cutoff test_hux_battery_cutoff.c)
target_include_directories(test_hux_battery_cutoff PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_battery_cutoff PRIVATE hux_battery_cutoff)
add_test(NAME hux_battery_cutoff COMMAND test_hux_battery_cutoff)

add_executable(test_hux_wifi_list test_hux_wifi_list.c)
target_include_directories(test_hux_wifi_list PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_wifi_list PRIVATE hux_wifi_list)
add_test(NAME hux_wifi_list COMMAND test_hux_wifi_list)

add_executable(test_hux_echo test_hux_echo.c)
target_include_directories(test_hux_echo PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_echo PRIVATE hux_echo m)
add_test(NAME hux_echo COMMAND test_hux_echo)

add_executable(test_hux_alert_logic test_hux_alert_logic.c)
target_include_directories(test_hux_alert_logic PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_alert_logic PRIVATE hux_alert_logic)
add_test(NAME hux_alert_logic COMMAND test_hux_alert_logic)

add_executable(test_hux_status test_hux_status.c)
target_include_directories(test_hux_status PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_status PRIVATE hux_status)
add_test(NAME hux_status COMMAND test_hux_status)

add_executable(test_hux_led_anim test_hux_led_anim.c)
target_include_directories(test_hux_led_anim PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_led_anim PRIVATE hux_led_anim)
add_test(NAME hux_led_anim COMMAND test_hux_led_anim)

# -- hux_ota_policy (library under test): crash-loop rollback (F-0023), in hux_ota.
set(HUX_OTA_DIR ${CMAKE_SOURCE_DIR}/../components/hux_ota)
add_library(hux_ota_policy STATIC ${HUX_OTA_DIR}/hux_ota_policy.c)
target_include_directories(hux_ota_policy PUBLIC ${HUX_OTA_DIR}/include)
add_executable(test_hux_ota_policy test_hux_ota_policy.c)
target_include_directories(test_hux_ota_policy PRIVATE ${CMAKE_SOURCE_DIR}/helpers)
target_link_libraries(test_hux_ota_policy PRIVATE hux_ota_policy)
add_test(NAME hux_ota_policy COMMAND test_hux_ota_policy)

# Convenience target: `cmake --build build --target check` runs every test.
add_custom_target(check
    COMMAND ${CMAKE_CTEST_COMMAND} --output-on-failure
    DEPENDS test_hux_proto test_hux_ws_frag test_hux_debounce test_hux_type_probe
            test_hux_wifi_retry test_hux_link test_hux_blackbox test_hux_super_check
            test_hux_battery_level test_hux_prompt_parse test_hux_battery_cutoff
            test_hux_wifi_list test_hux_echo test_hux_alert_logic test_hux_status
            test_hux_led_anim test_hux_ota_policy
)
