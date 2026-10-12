idf_component_register(
    SRCS "hux_console.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_system esp_wifi esp_timer hal hux_audio hux_battery hux_button hux_net hux_ota
)
