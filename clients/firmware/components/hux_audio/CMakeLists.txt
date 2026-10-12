idf_component_register(
    SRCS "hux_audio.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_board esp_driver_i2s
    PRIV_REQUIRES log freertos esp_codec_dev esp_driver_i2c esp_partition esp_timer esp_hw_support nvs_flash
)
