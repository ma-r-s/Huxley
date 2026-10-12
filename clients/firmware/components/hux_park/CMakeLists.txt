idf_component_register(
    SRCS "hux_park.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_hw_support esp_system esp_driver_gpio hux_audio hux_battery hux_board hux_net hux_opus hux_ota hux_prompts
)
