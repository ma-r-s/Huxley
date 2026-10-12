idf_component_register(
    SRCS "hux_battery.c" "hux_battery_level.c" "hux_battery_cutoff.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_adc esp_driver_gpio esp_timer nvs_flash hux_audio
)
