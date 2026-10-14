idf_component_register(
    SRCS "hux_prompts.c" "hux_prompt_parse.c"
    INCLUDE_DIRS "include"
    PRIV_REQUIRES log hux_opus
    EMBED_FILES "prompts/offline.hxp" "prompts/battery_empty.hxp"
                "prompts/startup.hxp" "prompts/searching.hxp" "prompts/connected.hxp"
                "prompts/lost.hxp" "prompts/updating.hxp"
                "prompts/no_wifi.hxp" "prompts/no_internet.hxp"
)
