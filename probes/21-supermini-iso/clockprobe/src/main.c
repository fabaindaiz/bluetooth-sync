/*
 * Experiment 21 clock probe for the SuperMini nRF52840 (throwaway, d-7c8794-3208b7).
 *
 * 1. Before the kernel touches LFCLK, try the 32.768 kHz crystal (LFXO) with a 2 s timeout,
 *    and if it starts, measure it for 1 s against the 32 MHz crystal (HFXO). Then stop it:
 *    the rest of the boot runs on the calibrated RC, so a missing crystal cannot hang the board.
 * 2. Every LF second, measure the running LF clock against HFXO in hardware:
 *    RTC2 COMPARE0 -> PPI -> TIMER1 CAPTURE0 (16 MHz).
 * 3. If the host sets the CDC line to 1200 baud, reboot into the UF2 bootloader (GPREGRET 0x57).
 *
 * Output lines (USB CDC console):
 *   X <started> <start_ms> <xtal_ppm_x100> <lfclkstat_hex>   crystal test, repeated every 10 s
 *   S <seq> <hf_ticks> <lf_ppm_x100> <hf_total_lo>             one per LF second
 * lf_ppm > 0 means the LF clock runs fast against HFXO.
 */

#include <zephyr/kernel.h>
#include <zephyr/init.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/drivers/clock_control/nrf_clock_control.h>
#include <zephyr/sys/printk.h>
#include <nrfx.h>

#define HF_TICKS_PER_S 16000000LL
#define LF_TICKS_PER_S 32768U
#define PPI_CH 19
#define UF2_MAGIC 0x57

static volatile uint32_t xtal_started;
static volatile uint32_t xtal_start_ms;
static volatile int32_t xtal_ppm_x100;
static volatile uint32_t xtal_lfclkstat;

static inline uint32_t timer1_now(void)
{
	NRF_TIMER1->TASKS_CAPTURE[1] = 1;
	return NRF_TIMER1->CC[1];
}

static void timer1_start(void)
{
	NRF_TIMER1->TASKS_STOP = 1;
	NRF_TIMER1->TASKS_CLEAR = 1;
	NRF_TIMER1->MODE = TIMER_MODE_MODE_Timer;
	NRF_TIMER1->BITMODE = TIMER_BITMODE_BITMODE_32Bit;
	NRF_TIMER1->PRESCALER = 0; /* 16 MHz */
	NRF_TIMER1->TASKS_START = 1;
}

static int32_t ppm_x100(uint32_t hf_ticks)
{
	/* LF second measured in HF ticks: fewer ticks = LF runs fast. */
	return (int32_t)((HF_TICKS_PER_S - (int64_t)hf_ticks) * 100000000LL / (int64_t)hf_ticks);
}

static int xtal_test(void)
{
	NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
	NRF_CLOCK->TASKS_HFCLKSTART = 1;
	while (!NRF_CLOCK->EVENTS_HFCLKSTARTED) {
	}
	timer1_start();

	NRF_CLOCK->TASKS_LFCLKSTOP = 1;
	while (NRF_CLOCK->LFCLKSTAT & CLOCK_LFCLKSTAT_STATE_Msk) {
	}
	NRF_CLOCK->LFCLKSRC = CLOCK_LFCLKSRC_SRC_Xtal;
	NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
	uint32_t t0 = timer1_now();
	NRF_CLOCK->TASKS_LFCLKSTART = 1;
	while (!NRF_CLOCK->EVENTS_LFCLKSTARTED) {
		if (timer1_now() - t0 > 2 * HF_TICKS_PER_S) {
			break;
		}
	}
	xtal_lfclkstat = NRF_CLOCK->LFCLKSTAT;
	if (NRF_CLOCK->EVENTS_LFCLKSTARTED) {
		xtal_started = 1;
		xtal_start_ms = (timer1_now() - t0) / 16000;
		/* One LF second against HFXO, from one RTC0 tick edge to another. */
		NRF_RTC0->TASKS_STOP = 1;
		NRF_RTC0->TASKS_CLEAR = 1;
		NRF_RTC0->PRESCALER = 0;
		NRF_RTC0->TASKS_START = 1;
		uint32_t guard = timer1_now();
		uint32_t c = NRF_RTC0->COUNTER;
		while (NRF_RTC0->COUNTER == c && timer1_now() - guard < HF_TICKS_PER_S) {
		}
		uint32_t start_count = NRF_RTC0->COUNTER;
		uint32_t a = timer1_now();
		while (((NRF_RTC0->COUNTER - start_count) & 0xFFFFFF) < LF_TICKS_PER_S &&
		       timer1_now() - a < 2 * HF_TICKS_PER_S) {
		}
		uint32_t b = timer1_now();
		xtal_ppm_x100 = ppm_x100(b - a);
		NRF_RTC0->TASKS_STOP = 1;
		NRF_RTC0->TASKS_CLEAR = 1;
	}
	/* Leave LFCLK stopped on RC, as reset leaves it, for the clock driver. */
	NRF_CLOCK->TASKS_LFCLKSTOP = 1;
	uint32_t s = timer1_now();
	while ((NRF_CLOCK->LFCLKSTAT & CLOCK_LFCLKSTAT_STATE_Msk) && timer1_now() - s < HF_TICKS_PER_S) {
	}
	NRF_CLOCK->LFCLKSRC = CLOCK_LFCLKSRC_SRC_RC;
	NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
	NRF_TIMER1->TASKS_STOP = 1;
	NRF_CLOCK->TASKS_HFCLKSTOP = 1;
	NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
	return 0;
}

/* Priority 0 of PRE_KERNEL_1: before the clock driver and the system timer start LFCLK. */
SYS_INIT(xtal_test, PRE_KERNEL_1, 0);

static void report_xtal(void)
{
	printk("X %u %u %d %08x\n", xtal_started, xtal_start_ms, xtal_ppm_x100, xtal_lfclkstat);
}

static void maybe_bootloader(const struct device *console)
{
	uint32_t baud = 0;

	if (uart_line_ctrl_get(console, UART_LINE_CTRL_BAUD_RATE, &baud) == 0 && baud == 1200) {
		printk("B going to UF2 bootloader\n");
		k_msleep(50);
		NRF_POWER->GPREGRET = UF2_MAGIC;
		NVIC_SystemReset();
	}
}

int main(void)
{
	const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	struct onoff_manager *hf = z_nrf_clock_control_get_onoff(CLOCK_CONTROL_NRF_SUBSYS_HF);
	struct onoff_client client;
	int result;

	sys_notify_init_spinwait(&client.notify);
	onoff_request(hf, &client);
	while (sys_notify_fetch_result(&client.notify, &result) == -EAGAIN) {
		k_msleep(1);
	}

	timer1_start();
	NRF_RTC2->TASKS_STOP = 1;
	NRF_RTC2->TASKS_CLEAR = 1;
	NRF_RTC2->PRESCALER = 0;
	NRF_RTC2->EVTENSET = RTC_EVTEN_COMPARE0_Msk;
	NRF_RTC2->CC[0] = LF_TICKS_PER_S;
	NRF_RTC2->EVENTS_COMPARE[0] = 0;
	NRF_PPI->CH[PPI_CH].EEP = (uint32_t)&NRF_RTC2->EVENTS_COMPARE[0];
	NRF_PPI->CH[PPI_CH].TEP = (uint32_t)&NRF_TIMER1->TASKS_CAPTURE[0];
	NRF_PPI->CHENSET = BIT(PPI_CH);
	NRF_RTC2->TASKS_START = 1;

	uint32_t seq = 0;
	uint32_t previous = 0;
	uint32_t total = 0;
	bool have_previous = false;

	while (true) {
		maybe_bootloader(console);
		if (NRF_RTC2->EVENTS_COMPARE[0]) {
			NRF_RTC2->EVENTS_COMPARE[0] = 0;
			uint32_t captured = NRF_TIMER1->CC[0];
			NRF_RTC2->CC[0] = (NRF_RTC2->CC[0] + LF_TICKS_PER_S) & 0xFFFFFF;
			if (have_previous) {
				uint32_t ticks = captured - previous;
				total += ticks;
				printk("S %u %u %d %u\n", seq, ticks, ppm_x100(ticks), total);
				seq++;
			}
			previous = captured;
			have_previous = true;
			if (seq % 10 == 0) {
				report_xtal();
			}
		}
		k_msleep(20);
	}
	return 0;
}
