idf_component_register(
    SRCS "hux_super.c" "hux_super_check.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_timer esp_system hux_app hux_audio hux_button hux_log hux_net hux_opus
)
