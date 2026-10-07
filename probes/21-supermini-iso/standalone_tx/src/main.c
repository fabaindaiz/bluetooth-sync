/*
 * Standalone emitter for experiment 21 (throwaway, d-7c8794-3208b7).
 *
 * Emits the same BIG the host probes create (tx_big.py): 4 BIS, SDU every 10 ms, 120 B, 2M PHY,
 * RTN 4 (the SDC chooses NSE 2 / IRC 2), from the static address F2:00:00:00:00:21 with SID 3, so
 * rx_big.py on the other board finds it unchanged. Each SDU carries <u32 frame counter><u8 BIS index>
 * and zero padding. The host here is Zephyr's, inside the board: no PC is needed, only power.
 *
 * The LED blinks once per second while the BIG runs, and stays on if something failed.
 * Setting the USB console to 1200 baud reboots into the UF2 bootloader (GPREGRET 0x57).
 */

#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/iso.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/net_buf.h>
#include <zephyr/sys/byteorder.h>
#include <nrfx.h>

#define NUM_BIS 4
#define SDU_LEN 120
#define SDU_INTERVAL_US 10000
#define IN_FLIGHT_PER_BIS 2
#define EMITTER_SID 3
#define UF2_MAGIC 0x57

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_ALIAS(led0), gpios);

NET_BUF_POOL_FIXED_DEFINE(tx_pool, NUM_BIS * IN_FLIGHT_PER_BIS,
			  BT_ISO_SDU_BUF_SIZE(SDU_LEN), CONFIG_BT_CONN_TX_USER_DATA_SIZE, NULL);

static K_SEM_DEFINE(sem_big_started, 0, NUM_BIS);
static K_SEM_DEFINE(sem_tx_credits, NUM_BIS * IN_FLIGHT_PER_BIS, NUM_BIS * IN_FLIGHT_PER_BIS);

static void iso_connected(struct bt_iso_chan *chan)
{
	/* Without an HCI data path the controller keeps the BIG but drops every SDU silently. */
	const struct bt_iso_chan_path hci_path = {
		.pid = BT_ISO_DATA_PATH_HCI,
		.format = BT_HCI_CODING_FORMAT_TRANSPARENT,
	};
	int err = bt_iso_setup_data_path(chan, BT_HCI_DATAPATH_DIR_HOST_TO_CTLR, &hci_path);

	if (err) {
		printk("data path: %d\n", err);
	}
	k_sem_give(&sem_big_started);
}

static void iso_disconnected(struct bt_iso_chan *chan, uint8_t reason)
{
	printk("BIS disconnected, reason 0x%02x\n", reason);
}

static atomic_t sent_count;
static atomic_t frame_count;
static atomic_t last_send_err;

static void iso_sent(struct bt_iso_chan *chan)
{
	atomic_inc(&sent_count);
	k_sem_give(&sem_tx_credits);
}

/* Heartbeat: frames queued, SDUs reported sent, free credits, last send error. */
static void heartbeat_thread(void *a, void *b, void *c)
{
	while (true) {
		k_msleep(2000);
		printk("hb frame=%ld sent=%ld credits=%u err=%ld\n", (long)atomic_get(&frame_count),
		       (long)atomic_get(&sent_count), k_sem_count_get(&sem_tx_credits),
		       (long)atomic_get(&last_send_err));
	}
}

K_THREAD_DEFINE(heartbeat, 768, heartbeat_thread, NULL, NULL, NULL, K_LOWEST_APPLICATION_THREAD_PRIO, 0, 0);

static struct bt_iso_chan_ops iso_ops = {
	.connected = iso_connected,
	.disconnected = iso_disconnected,
	.sent = iso_sent,
};

static struct bt_iso_chan_io_qos tx_qos = {
	.sdu = SDU_LEN,
	.rtn = 4,
	.phy = BT_GAP_LE_PHY_2M,
};

static struct bt_iso_chan_qos chan_qos = {.tx = &tx_qos};
static struct bt_iso_chan chans[NUM_BIS];
static struct bt_iso_chan *chan_ptrs[NUM_BIS];

static void fail(const char *what, int err)
{
	printk("FAIL %s: %d\n", what, err);
	gpio_pin_set_dt(&led, 1);
	while (true) {
		k_sleep(K_FOREVER);
	}
}

static void touch_thread(void *a, void *b, void *c)
{
	const struct device *console = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
	uint32_t baud;

	while (true) {
		k_msleep(100);
		if (uart_line_ctrl_get(console, UART_LINE_CTRL_BAUD_RATE, &baud) == 0 && baud == 1200) {
			NRF_POWER->GPREGRET = UF2_MAGIC;
			NVIC_SystemReset();
		}
	}
}

K_THREAD_DEFINE(touch, 512, touch_thread, NULL, NULL, NULL, K_LOWEST_APPLICATION_THREAD_PRIO, 0, 0);

int main(void)
{
	bt_addr_le_t addr = {.type = BT_ADDR_LE_RANDOM, .a = {.val = {0x21, 0, 0, 0, 0, 0xF2}}};
	struct bt_le_ext_adv *adv;
	struct bt_iso_big *big;
	int err;

	gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);

	/* Before bt_enable() this sets the default identity: the probes' emitter address. */
	err = bt_id_create(&addr, NULL);
	if (err < 0) {
		fail("bt_id_create", err);
	}
	err = bt_enable(NULL);
	if (err) {
		fail("bt_enable", err);
	}

	struct bt_le_adv_param adv_param = {
		.id = BT_ID_DEFAULT,
		.sid = EMITTER_SID,
		.options = BT_LE_ADV_OPT_EXT_ADV | BT_LE_ADV_OPT_USE_IDENTITY,
		.interval_min = BT_GAP_MS_TO_ADV_INTERVAL(100),
		.interval_max = BT_GAP_MS_TO_ADV_INTERVAL(150),
	};
	static const struct bt_data ad[] = {
		BT_DATA(BT_DATA_NAME_COMPLETE, CONFIG_BT_DEVICE_NAME, sizeof(CONFIG_BT_DEVICE_NAME) - 1),
	};

	err = bt_le_ext_adv_create(&adv_param, NULL, &adv);
	if (err) {
		fail("ext_adv_create", err);
	}
	err = bt_le_ext_adv_set_data(adv, ad, ARRAY_SIZE(ad), NULL, 0);
	if (err) {
		fail("ext_adv_set_data", err);
	}
	err = bt_le_per_adv_set_param(adv, BT_LE_PER_ADV_PARAM(BT_GAP_MS_TO_PER_ADV_INTERVAL(80),
							       BT_GAP_MS_TO_PER_ADV_INTERVAL(100),
							       BT_LE_PER_ADV_OPT_NONE));
	if (err) {
		fail("per_adv_set_param", err);
	}
	err = bt_le_per_adv_start(adv);
	if (err) {
		fail("per_adv_start", err);
	}
	err = bt_le_ext_adv_start(adv, BT_LE_EXT_ADV_START_DEFAULT);
	if (err) {
		fail("ext_adv_start", err);
	}

	for (int i = 0; i < NUM_BIS; i++) {
		chans[i].ops = &iso_ops;
		chans[i].qos = &chan_qos;
		chan_ptrs[i] = &chans[i];
	}
	struct bt_iso_big_create_param big_param = {
		.bis_channels = chan_ptrs,
		.num_bis = NUM_BIS,
		.interval = SDU_INTERVAL_US,
		.latency = 60,
		.packing = BT_ISO_PACKING_SEQUENTIAL,
		.framing = BT_ISO_FRAMING_UNFRAMED,
	};
	err = bt_iso_big_create(adv, &big_param, &big);
	if (err) {
		fail("big_create", err);
	}
	for (int i = 0; i < NUM_BIS; i++) {
		k_sem_take(&sem_big_started, K_FOREVER);
	}
	printk("BIG running: 4 BIS, %d B every %d us\n", SDU_LEN, SDU_INTERVAL_US);

	uint8_t sdu[SDU_LEN];
	uint32_t frame = 0;

	while (true) {
		for (int i = 0; i < NUM_BIS; i++) {
			k_sem_take(&sem_tx_credits, K_FOREVER);
			struct net_buf *buf = net_buf_alloc(&tx_pool, K_FOREVER);

			net_buf_reserve(buf, BT_ISO_CHAN_SEND_RESERVE);
			memset(sdu, 0, sizeof(sdu));
			sys_put_le32(frame, sdu);
			sdu[4] = i + 1;
			net_buf_add_mem(buf, sdu, sizeof(sdu));
			err = bt_iso_chan_send(&chans[i], buf, (uint16_t)frame);
			if (err < 0) {
				atomic_set(&last_send_err, err);
				net_buf_unref(buf);
				k_sem_give(&sem_tx_credits);
				printk("send BIS %d frame %u: %d\n", i + 1, frame, err);
			}
		}
		frame++;
		atomic_set(&frame_count, frame);
		if (frame % 100 == 0) {
			gpio_pin_toggle_dt(&led);
		}
		if (frame % 6000 == 0) {
			printk("frame %u\n", frame);
		}
	}
	return 0;
}
