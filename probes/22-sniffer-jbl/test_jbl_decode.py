"""Offline tests of jbl_decode.py (experiment 22). No radio, no serial port.

Run from this directory with the host's Python (it has Bumble, used as an independent
implementation to build and parse the same bytes):
    python -m unittest -v test_jbl_decode
Without Bumble, the cross-checks are skipped and the rest still runs.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import struct
import tempfile
import unittest

import jbl_decode as d

try:
    from bumble import hci
    from bumble.profiles import bap, le_audio
except ImportError:  # pragma: no cover
    hci = None

# Measured in experiment 01 (Mac, CoreBluetooth, 2026-09-26).
GO4_IDLE = bytes.fromhex('e420013830 2cf1330900'.replace(' ', ''))
CHARGE6_BROADCASTING = bytes.fromhex('e3201439ed0e883f0b00')
GO4_STEREO = bytes.fromhex('e42002d4ed0e5d9e0960')


def ad(t: int, v: bytes) -> bytes:
    return bytes([len(v) + 1, t]) + v


def service_data(uuid: int, v: bytes) -> bytes:
    return ad(0x16, uuid.to_bytes(2, 'little') + v)


def harman(v: bytes) -> bytes:
    return ad(0xFF, d.HARMAN_COMPANY_ID.to_bytes(2, 'little') + v)


def pack_biginfo(**f) -> bytes:
    """Independent packer, written from the masks in Wireshark's packet-bthci_cmd.c."""
    w0 = f['big_offset'] | f['units'] << 14 | f['iso_interval'] << 15 | f['num_bis'] << 27
    w1 = f['nse'] | f['bn'] << 5 | f['sub_interval'] << 8 | f['pto'] << 28
    w2 = f['bis_spacing'] | f['irc'] << 20
    w3 = f['sdu_interval'] | f['max_sdu'] << 20
    w4 = f['chm'] | f['phy'] << 37
    w5 = f['payload_count'] | f['framing'] << 39
    return (struct.pack('<II', w0, w1) + w2.to_bytes(3, 'little') + bytes([f['max_pdu'], 0])
            + struct.pack('<I', f['seed']) + struct.pack('<I', w3) + f['crc'].to_bytes(2, 'little')
            + w4.to_bytes(5, 'little') + w5.to_bytes(5, 'little'))


BIGINFO = dict(big_offset=100, units=0, iso_interval=8, num_bis=2, nse=4, bn=1, sub_interval=2500,
               pto=0, bis_spacing=1250, irc=4, max_pdu=100, seed=0x12345678, sdu_interval=10000,
               max_sdu=100, crc=0xBEEF, chm=0x1FFFFFFFFF, phy=1, payload_count=12345, framing=0)


def run(path: str) -> list[dict]:
    dec = d.Decoder(show_all=False)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for t, kind, data in d.read_capture(path):
            dec.nordic(t, data) if kind == 'nordic' else dec.h4(t, data)
        dec.summary()
    return [json.loads(line) for line in out.getvalue().splitlines()]


class TestFields(unittest.TestCase):
    def test_jbl_idle_and_broadcast_formats(self):
        idle = d.decode_jbl_manufacturer(GO4_IDLE)
        self.assertEqual(idle['model'], 'Go 4')
        self.assertFalse(idle['hyp_stereo_group'])
        self.assertTrue(d.decode_jbl_manufacturer(CHARGE6_BROADCASTING)['hyp_broadcasting'])
        self.assertEqual(d.decode_jbl_manufacturer(CHARGE6_BROADCASTING)['model'], 'Charge 6')
        self.assertTrue(d.decode_jbl_manufacturer(GO4_STEREO)['hyp_stereo_group'])
        self.assertEqual(d.decode_jbl_manufacturer(d.JBL_BROADCAST_TAG)['format'], 'broadcast_tag')

    def test_charge6_broadcast_ad(self):
        # Experiment 01, result 3: Broadcast_ID 0x112233 and PBP features 0x04, no metadata.
        data = (harman(d.JBL_BROADCAST_TAG) + service_data(0x1852, bytes.fromhex('332211'))
                + service_data(0x1856, b'\x04\x00') + ad(0x09, b'JBL Charge 6'))
        f = d.decode_ad_list(d.parse_ad(data))
        self.assertEqual(f['broadcast_id'], '0x112233')
        self.assertEqual(f['pbp']['high_quality'], True)
        self.assertFalse(f['pbp']['encrypted'])
        self.assertEqual(f['jbl']['format'], 'broadcast_tag')
        self.assertEqual(f['name'], 'JBL Charge 6')

    def test_biginfo_against_wireshark_masks(self):
        b = d.decode_biginfo(pack_biginfo(**BIGINFO))
        self.assertEqual((b['num_bis'], b['nse'], b['bn'], b['pto'], b['irc']), (2, 4, 1, 0, 4))
        self.assertEqual(b['iso_interval_ms'], 10.0)
        self.assertEqual((b['sub_interval_us'], b['bis_spacing_us'], b['max_pdu']), (2500, 1250, 100))
        self.assertEqual((b['sdu_interval_us'], b['max_sdu'], b['phy']), (10000, 100, '2M'))
        self.assertEqual((b['bis_payload_count'], b['framed'], b['encrypted']), (12345, False, False))
        self.assertEqual(b['big_offset_us'], 3000)
        self.assertEqual(b['seed_access_address'], '0x12345678')

    @unittest.skipIf(hci is None, 'Bumble not installed')
    def test_base_against_bumble(self):
        csc = bap.CodecSpecificConfiguration
        base = bap.BasicAudioAnnouncement(
            presentation_delay=40000,
            subgroups=[bap.BasicAudioAnnouncement.Subgroup(
                codec_id=hci.CodingFormat(hci.CodecID.LC3),
                codec_specific_configuration=csc(
                    sampling_frequency=bap.SamplingFrequency.FREQ_48000,
                    frame_duration=bap.FrameDuration.DURATION_10000_US,
                    octets_per_codec_frame=100),
                metadata=le_audio.Metadata(),
                bis=[bap.BasicAudioAnnouncement.BIS(1, csc(audio_channel_allocation=bap.AudioLocation.FRONT_LEFT)),
                     bap.BasicAudioAnnouncement.BIS(2, csc(audio_channel_allocation=bap.AudioLocation.FRONT_RIGHT))])])
        out = d.decode_base(bytes(base))
        self.assertNotIn('error', out)
        self.assertEqual(out['presentation_delay_us'], 40000)
        sub = out['subgroups'][0]
        self.assertTrue(sub['lc3'])
        self.assertEqual((sub['config']['sampling_hz'], sub['config']['frame_ms'], sub['config']['octets_per_frame']),
                         (48000, 10.0, 100))
        self.assertEqual([b['config']['locations'] for b in sub['bis']], [['FL'], ['FR']])
        # The same bytes, parsed back by Bumble.
        again = bap.BasicAudioAnnouncement.from_bytes(bytes(base))
        self.assertEqual(again.subgroups[0].bis[1].codec_specific_configuration.audio_channel_allocation,
                         bap.AudioLocation.FRONT_RIGHT)

    def test_audio_location_names_match_bumble(self):
        if hci is None:
            self.skipTest('Bumble not installed')
        self.assertEqual(len(d.AUDIO_LOCATIONS), 28)
        expected = {0: 'FRONT_LEFT', 1: 'FRONT_RIGHT', 3: 'LOW_FREQUENCY_EFFECTS_1', 4: 'BACK_LEFT',
                    10: 'SIDE_LEFT', 11: 'SIDE_RIGHT', 26: 'LEFT_SURROUND', 27: 'RIGHT_SURROUND'}
        for bit, name in expected.items():
            self.assertEqual(bap.AudioLocation(1 << bit).name, name, d.AUDIO_LOCATIONS[bit])


class TestNordicPcap(unittest.TestCase):
    """A synthetic nRF Sniffer capture: AUX_ADV_IND with SyncInfo, then AUX_SYNC_IND with BASE + BIGInfo."""

    def nordic_record(self, channel: int, aux_type: int, aa: int, pdu: bytes, ts: int, counter: int) -> bytes:
        ll = struct.pack('<I', aa) + bytes([0x07, len(pdu)]) + pdu + b'\x00\x00\x00'
        flags = 0x01 | aux_type << 1 | 1 << 4  # CRC ok, 2M
        payload_hdr = bytes([10, flags, channel, 50]) + struct.pack('<HI', 0, ts)
        body = payload_hdr + ll
        return bytes([0]) + struct.pack('<H', len(body)) + bytes([3]) + struct.pack('<H', counter) + b'\x02' + body

    def ext_pdu(self, adv_a: bytes | None, sync_aa: int | None, acad: bytes, adv_data: bytes) -> bytes:
        flags, hdr = 0, b''
        if adv_a:
            flags |= 0x01
            hdr += adv_a
        flags |= 0x08
        hdr += struct.pack('<H', 1 << 12 | 7)  # SID 1, DID 7
        if sync_aa is not None:
            flags |= 0x20
            hdr += struct.pack('<HH', 100, 80) + b'\xff\xff\xff\xff\x1f' + struct.pack('<I', sync_aa) + b'\x55\x55\x55\x00\x00'
        hdr = bytes([flags]) + hdr + acad
        return bytes([len(hdr)]) + hdr + adv_data

    def test_end_to_end(self):
        adv_a = bytes.fromhex('6d66da60f290')  # 90:F2:60:DA:66:6D, little-endian on air
        pa_aa = 0x5A5A1234
        aux_adv = self.ext_pdu(adv_a, pa_aa, b'', harman(d.JBL_BROADCAST_TAG) + service_data(0x1852, bytes.fromhex('058100')))
        base = bytes.fromhex('409c00' '01' '02' '0600000000' '0a' '020108' '020201' '03046400' '00'
                             '01' '06' '0503010000' '00' '02' '06' '0503020000' '00')
        aux_sync = lambda n: self.ext_pdu(None, None, ad(0x2C, pack_biginfo(**BIGINFO)), service_data(0x1851, base))
        records = [self.nordic_record(10, 0, d.ADV_ACCESS_ADDRESS, aux_adv, 1_000_000, 1)]
        for k in range(5):
            records.append(self.nordic_record(20, 2, pa_aa, aux_sync(k), 1_100_000 + 100_000 * k + (k % 2), 2 + k))
        with tempfile.NamedTemporaryFile(suffix='.pcap', delete=False) as f:
            f.write(struct.pack('<IHHiIII', 0xA1B2C3D4, 2, 4, 0, 0, 65535, 272))
            for k, r in enumerate(records):
                f.write(struct.pack('<IIII', 1000 + k, 0, len(r), len(r)) + r)
        try:
            evs = run(f.name)
        finally:
            os.unlink(f.name)
        summary = [e for e in evs if e['event'] == 'summary' and e['who'] == '90:F2:60:DA:66:6D']
        self.assertEqual(len(summary), 1, evs)
        s = summary[0]
        self.assertEqual(s['broadcast_ids'], ['0x008105'])
        self.assertEqual(s['pdus'], {'AUX_ADV_IND': 1, 'AUX_SYNC_IND': 5})
        self.assertEqual(len(s['base']), 1)
        self.assertEqual([b['config']['locations'] for b in s['base'][0]['subgroups'][0]['bis']], [['FL'], ['FR']])
        self.assertEqual(s['base'][0]['presentation_delay_us'], 40000)
        self.assertEqual(s['biginfo'][0]['num_bis'], 2)
        self.assertEqual(s['aux_sync_interval_us']['n'], 4)
        sync_ev = [e for e in evs if e.get('event') == 'AUX_SYNC_IND']
        self.assertEqual(sync_ev[0]['biginfo']['big_anchor_fw_us'], 1_100_000 + 3000)


@unittest.skipIf(hci is None, 'Bumble not installed')
class TestBtsnoop(unittest.TestCase):
    """HCI events built by hand, checked against Bumble's parser, then decoded from a btsnoop file."""

    def meta(self, sub: int, params: bytes) -> bytes:
        return bytes([0x04, 0x3E, len(params) + 1, sub]) + params

    def test_hci_path(self):
        addr = bytes.fromhex('b71d93f36678')  # 78:66:F3:93:1D:B7
        data = harman(d.JBL_BROADCAST_TAG) + service_data(0x1852, bytes.fromhex('332211'))
        ext = (b'\x01' + struct.pack('<H', 0x0000) + b'\x00' + addr + b'\x01\x02\x03\x7f' + struct.pack('b', -40)
               + struct.pack('<H', 80) + b'\x00' + bytes(6) + bytes([len(data)]) + data)
        established = b'\x00' + struct.pack('<H', 7) + b'\x03\x00' + addr + b'\x02' + struct.pack('<H', 80) + b'\x00'
        base = bytes.fromhex('409c00' '01' '01' '0600000000' '0a' '020108' '020201' '03046400' '00' '01' '00')
        pa_data = service_data(0x1851, base)
        pa = struct.pack('<H', 7) + b'\x7f' + struct.pack('b', -45) + b'\xff\x00' + bytes([len(pa_data)]) + pa_data
        big = (struct.pack('<H', 7) + b'\x01\x02' + struct.pack('<H', 8) + b'\x01\x00\x02' + struct.pack('<H', 100)
               + (10000).to_bytes(3, 'little') + struct.pack('<H', 100) + b'\x02\x00\x00')
        packets = [self.meta(0x0D, ext), self.meta(0x0E, established), self.meta(0x0F, pa), self.meta(0x22, big)]

        # The independent check: Bumble reads the same bytes as the same fields.
        ev = hci.HCI_Packet.from_bytes(packets[0])
        self.assertEqual(str(ev.reports[0].address).split('/')[0], '78:66:F3:93:1D:B7')
        self.assertEqual(ev.reports[0].periodic_advertising_interval, 80)
        bi = hci.HCI_Packet.from_bytes(packets[3])
        self.assertEqual((bi.num_bis, bi.nse, bi.irc, bi.max_sdu), (1, 2, 2, 100))
        self.assertEqual(hci.HCI_Packet.from_bytes(packets[2]).data, pa_data)

        with tempfile.NamedTemporaryFile(suffix='.btsnoop', delete=False) as f:
            f.write(b'btsnoop\0' + struct.pack('>II', 1, 1002))
            for k, p in enumerate(packets):
                f.write(struct.pack('>IIIIq', len(p), len(p), 3, 0, d.BTSNOOP_EPOCH_DELTA_US + k * 1_000_000) + p)
        try:
            evs = run(f.name)
        finally:
            os.unlink(f.name)
        s = [e for e in evs if e['event'] == 'summary' and e['who'] == '78:66:F3:93:1D:B7'][0]
        self.assertEqual(s['broadcast_ids'], ['0x112233'])
        self.assertEqual(s['base'][0]['subgroups'][0]['bis'][0]['index'], 1)
        self.assertEqual(s['biginfo'][0]['nse'], 2)
        self.assertEqual(s['pdus'], {'HCI_ext_report': 1, 'HCI_pa_report': 1, 'HCI_biginfo': 1})


if __name__ == '__main__':
    unittest.main()
