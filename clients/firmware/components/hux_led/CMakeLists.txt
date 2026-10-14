idf_component_register(
    SRCS "hux_led.c" "hux_led_anim.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_timer nvs_flash led_strip hux_alert hux_audio
)
