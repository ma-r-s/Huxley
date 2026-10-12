idf_component_register(
    SRCS "hux_opus.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log freertos esp_timer espressif__esp_audio_codec
)
