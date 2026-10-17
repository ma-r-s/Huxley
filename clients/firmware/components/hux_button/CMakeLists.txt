idf_component_register(
    SRCS "hux_button.c" "hux_debounce.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_app hux_board
    PRIV_REQUIRES log freertos esp_driver_gpio esp_timer
)
