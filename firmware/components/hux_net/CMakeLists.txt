idf_component_register(
    SRCS "hux_net.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_app esp_wifi esp_netif esp_event esp_websocket_client
    PRIV_REQUIRES log freertos
)
