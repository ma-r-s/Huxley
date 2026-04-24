#include "hux_board.h"

#include <string.h>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"

static const char *TAG = "hux_board";

#define I2C_SDA_GPIO        11
#define I2C_SCL_GPIO        10
#define I2C_FREQ_HZ         400000
#define TCA9555_I2C_ADDR    0x20

/* TCA9555 registers (datasheet §7.5). Two 8-bit ports (0 = P0..P7,
 * 1 = P8..P15); each function occupies two sequential addresses. */
#define TCA_REG_INPUT_0     0x00
#define TCA_REG_INPUT_1     0x01
#define TCA_REG_OUTPUT_0    0x02
#define TCA_REG_OUTPUT_1    0x03
#define TCA_REG_CONFIG_0    0x06
#define TCA_REG_CONFIG_1    0x07

/* Direction bit: 0 = output, 1 = input. Defaults chosen so the board
 * boots inert: LCD+touch in reset (low), camera off (P5=1), PA off
 * (P8=0). Anything not explicitly an output is an input (safer
 * default — a stray pull-up won't sink current into an output). */
#define TCA_CONFIG_PORT_0   0b11011100u /* inputs: P2,P3,P4,P6,P7 */
#define TCA_CONFIG_PORT_1   0b11111110u /* input: P9..P15; output: P8 */
/* Initial output-register values matching the "inert" state. */
#define TCA_OUTPUT_PORT_0   0b00100000u /* P5=1 (cam off); P0,P1 low */
#define TCA_OUTPUT_PORT_1   0b00000000u /* P8=0 (PA off) */

static i2c_master_bus_handle_t s_bus = NULL;
static i2c_master_dev_handle_t s_tca = NULL;
/* Cache the OUTPUT register so write-one-pin doesn't require a
 * read-modify-write round trip every call. Single writer (this
 * component). Guarded by s_tca_lock for cross-task set/get. */
static uint8_t s_tca_out[2] = {TCA_OUTPUT_PORT_0, TCA_OUTPUT_PORT_1};
static SemaphoreHandle_t s_tca_lock = NULL;

static bool tca_write_reg(uint8_t reg, uint8_t value) {
    uint8_t buf[2] = {reg, value};
    esp_err_t err = i2c_master_transmit(s_tca, buf, sizeof(buf), 100);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "tca.write reg=0x%02x err=%s", reg, esp_err_to_name(err));
        return false;
    }
    return true;
}

static bool tca_read_reg(uint8_t reg, uint8_t *value) {
    esp_err_t err = i2c_master_transmit_receive(s_tca, &reg, 1, value, 1, 100);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "tca.read reg=0x%02x err=%s", reg, esp_err_to_name(err));
        return false;
    }
    return true;
}

void hux_board_init(void) {
    if (s_bus != NULL) {
        return; /* Idempotent. */
    }

    s_tca_lock = xSemaphoreCreateMutex();
    configASSERT(s_tca_lock != NULL);

    i2c_master_bus_config_t bus_cfg = {
        .i2c_port = I2C_NUM_0,
        .sda_io_num = I2C_SDA_GPIO,
        .scl_io_num = I2C_SCL_GPIO,
        .clk_source = I2C_CLK_SRC_DEFAULT,
        .glitch_ignore_cnt = 7,
        .flags.enable_internal_pullup = true,
    };
    ESP_ERROR_CHECK(i2c_new_master_bus(&bus_cfg, &s_bus));

    i2c_device_config_t tca_cfg = {
        .dev_addr_length = I2C_ADDR_BIT_LEN_7,
        .device_address = TCA9555_I2C_ADDR,
        .scl_speed_hz = I2C_FREQ_HZ,
    };
    ESP_ERROR_CHECK(i2c_master_bus_add_device(s_bus, &tca_cfg, &s_tca));

    /* Order: write OUTPUTs before CONFIG so the pin drives the right
     * level on the first clock edge it becomes an output — skips a
     * glitch at boot that could release the LCD reset or wink the PA
     * on for a few microseconds. */
    bool ok = tca_write_reg(TCA_REG_OUTPUT_0, TCA_OUTPUT_PORT_0) &&
              tca_write_reg(TCA_REG_OUTPUT_1, TCA_OUTPUT_PORT_1) &&
              tca_write_reg(TCA_REG_CONFIG_0, TCA_CONFIG_PORT_0) &&
              tca_write_reg(TCA_REG_CONFIG_1, TCA_CONFIG_PORT_1);
    if (!ok) {
        ESP_LOGE(TAG, "tca.init_failed — expander not responding at 0x%02x",
                 TCA9555_I2C_ADDR);
        return;
    }

    ESP_LOGI(TAG, "board.init i2c=sda%d/scl%d tca=0x%02x",
             I2C_SDA_GPIO, I2C_SCL_GPIO, TCA9555_I2C_ADDR);
}

bool hux_board_tca_read(uint8_t pin) {
    if (s_tca == NULL || pin > 15) {
        return false;
    }
    uint8_t reg = (pin < 8) ? TCA_REG_INPUT_0 : TCA_REG_INPUT_1;
    uint8_t value = 0;
    if (!tca_read_reg(reg, &value)) {
        return false; /* Fail-safe: unreadable = not pressed. */
    }
    return (value >> (pin & 0x07)) & 0x01;
}

bool hux_board_tca_write(uint8_t pin, bool high) {
    if (s_tca == NULL || pin > 15) {
        return false;
    }
    bool ok;
    xSemaphoreTake(s_tca_lock, portMAX_DELAY);
    uint8_t port = (pin < 8) ? 0 : 1;
    uint8_t bit = 1u << (pin & 0x07);
    uint8_t next = high ? (s_tca_out[port] | bit) : (s_tca_out[port] & ~bit);
    if (next == s_tca_out[port]) {
        xSemaphoreGive(s_tca_lock);
        return true; /* No change. */
    }
    uint8_t reg = port == 0 ? TCA_REG_OUTPUT_0 : TCA_REG_OUTPUT_1;
    ok = tca_write_reg(reg, next);
    if (ok) {
        s_tca_out[port] = next;
    }
    xSemaphoreGive(s_tca_lock);
    return ok;
}

i2c_master_bus_handle_t hux_board_i2c_bus(void) {
    return s_bus;
}
