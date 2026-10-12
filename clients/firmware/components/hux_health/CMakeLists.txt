idf_component_register(
    SRCS "hux_health.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_timer heap json hux_audio hux_battery hux_net hux_ota
)
