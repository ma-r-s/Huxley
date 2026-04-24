idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES hux_app hux_audio hux_board hux_button hux_log hux_net
    PRIV_REQUIRES nvs_flash esp_event esp_system esp_app_format
)
