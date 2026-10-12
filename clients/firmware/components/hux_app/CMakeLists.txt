idf_component_register(
    SRCS "hux_app.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_proto
    PRIV_REQUIRES log freertos json mbedtls hux_audio hux_log hux_net hux_opus
)
