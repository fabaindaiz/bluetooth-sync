"""The project's local fixes to Bumble (aurasync.bumble_fixes), checked on the pinned Bumble."""

from bumble import hci

from aurasync import bumble_fixes

# The SoftDevice Controller's real reply to LE Read ISO TX Sync on a BIS, captured on a SuperMini
# nRF52840 (docs/research/experimentos/21-f1-iso-en-la-supermini.md): Command Complete, handle
# 0x0133, packet sequence number 3, TX timestamp 92174997 µs, Time_Offset 0 (3 octets).
SDC_REPLY = bytes.fromhex("040e0f0161200033010300957a7e05000000")


def _time_offset_size() -> object:
    return dict(hci.HCI_LE_Read_ISO_TX_Sync_ReturnParameters.fields)["time_offset"]


def test_read_iso_tx_sync_reply_parses_after_the_fix():
    bumble_fixes.apply()
    event = hci.HCI_Packet.from_bytes(SDC_REPLY)
    assert isinstance(event, hci.HCI_Command_Complete_Event)
    parameters = event.return_parameters
    assert isinstance(parameters, hci.HCI_LE_Read_ISO_TX_Sync_ReturnParameters)
    assert parameters.status == hci.HCI_SUCCESS
    assert parameters.connection_handle == 0x0133
    assert parameters.packet_sequence_number == 3
    assert parameters.tx_time_stamp == 92174997
    assert parameters.time_offset == 0


def test_time_offset_is_three_octets_and_apply_is_idempotent():
    bumble_fixes.apply()
    bumble_fixes.apply()
    assert _time_offset_size() == 3


def test_time_offset_uses_all_24_bits():
    bumble_fixes.apply()
    reply = bytearray(SDC_REPLY)
    reply[-3:] = (0x123456).to_bytes(3, "little")
    event = hci.HCI_Packet.from_bytes(bytes(reply))
    assert event.return_parameters.time_offset == 0x123456
