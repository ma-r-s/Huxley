idf_component_register(
    SRCS "hux_ota.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_https_ota esp_http_client app_update esp_app_format
                  esp_system esp_timer json mbedtls hux_audio hux_net
)
