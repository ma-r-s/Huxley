idf_component_register(
    SRCS "hux_alert.c" "hux_alert_logic.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_timer esp_http_client esp-tls mbedtls esp_netif lwip nvs_flash hux_net
)
