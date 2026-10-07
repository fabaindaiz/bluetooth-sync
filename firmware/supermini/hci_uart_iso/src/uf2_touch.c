/*
 * Reboot into the UF2 bootloader when the host sets the HCI CDC-ACM line to 1200 baud
 * (the Arduino "1200 baud touch"), so the board can be reflashed without a double reset.
 * The Adafruit nRF52 bootloader enters UF2 mode when GPREGRET holds 0x57.
 *
 * From the PC: stty -F /dev/ttyACMn 1200   (Bumble opens the port at 1000000 baud).
 */

#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <nrfx.h>

#define UF2_MAGIC 0x57
#define TOUCH_BAUD 1200
#define POLL_MS 100

static void uf2_touch_thread(void *a, void *b, void *c)
{
	const struct device *uart = DEVICE_DT_GET(DT_CHOSEN(zephyr_bt_c2h_uart));
	uint32_t baud;

	ARG_UNUSED(a);
	ARG_UNUSED(b);
	ARG_UNUSED(c);

	while (true) {
		k_msleep(POLL_MS);
		if (uart_line_ctrl_get(uart, UART_LINE_CTRL_BAUD_RATE, &baud) == 0 &&
		    baud == TOUCH_BAUD) {
			NRF_POWER->GPREGRET = UF2_MAGIC;
			NVIC_SystemReset();
		}
	}
}

K_THREAD_DEFINE(uf2_touch, 512, uf2_touch_thread, NULL, NULL, NULL, K_LOWEST_APPLICATION_THREAD_PRIO,
		0, 1000);
