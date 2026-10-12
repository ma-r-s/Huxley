idf_component_register(
    SRCS "hux_dev.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_http_server esp_partition esp_system esp_timer
                  esp_app_format hux_console hux_audio hux_net hux_ota
)
