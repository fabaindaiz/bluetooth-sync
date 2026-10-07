# The SuperMini's HCI controller: Zephyr's samples/bluetooth/hci_uart, compiled from the SDK in place
# (its source is referenced, not copied), plus uf2_touch.c. Build:
#   west build -b promicro_nrf52840/nrf52840/uf2 <repo>/firmware/supermini/hci_uart_iso -- \
#     -DEXTRA_CONF_FILE=iso.conf            (crystal)   or   "-DEXTRA_CONF_FILE=iso.conf;rc.conf"   (RC)
cmake_minimum_required(VERSION 3.20.0)
# The sample's own prj.conf (raw HCI over H4), read from the SDK.
set(CONF_FILE $ENV{ZEPHYR_BASE}/samples/bluetooth/hci_uart/prj.conf)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(hci_uart_iso)
target_sources(app PRIVATE ${ZEPHYR_BASE}/samples/bluetooth/hci_uart/src/main.c src/uf2_touch.c)
