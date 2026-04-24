idf_component_register(
    SRCS "hux_app.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_proto
    PRIV_REQUIRES log freertos mbedtls hux_audio hux_net
)
