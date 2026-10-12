idf_component_register(
    SRCS "hux_net.c" "hux_ws_frag.c" "hux_type_probe.c" "hux_wifi_retry.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_log esp_wifi esp_netif esp_event esp_websocket_client
    PRIV_REQUIRES log freertos json mbedtls esp_timer hux_app hux_opus
)
