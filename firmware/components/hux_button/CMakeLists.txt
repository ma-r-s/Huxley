idf_component_register(
    SRCS "hux_button.c"
    INCLUDE_DIRS "include"
    REQUIRES hux_app hux_board
    PRIV_REQUIRES log freertos
)
