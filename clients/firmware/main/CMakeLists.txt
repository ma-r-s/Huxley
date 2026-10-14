idf_component_register(
    SRCS "main.c"
    INCLUDE_DIRS "."
    REQUIRES hux_alert hux_app hux_audio hux_battery hux_board hux_button hux_console hux_dev hux_led hux_log hux_net hux_opus hux_ota hux_park hux_super hux_health
    PRIV_REQUIRES nvs_flash esp_event esp_system esp_app_format
)
