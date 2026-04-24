idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES hux_app hux_net
    PRIV_REQUIRES nvs_flash esp_event
)
