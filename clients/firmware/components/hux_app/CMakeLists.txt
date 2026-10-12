idf_component_register(
    SRCS "hux_app.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_proto
    PRIV_REQUIRES log freertos json mbedtls esp_timer esp_app_format espcoredump hux_audio hux_battery hux_log hux_net hux_opus hux_ota
)
