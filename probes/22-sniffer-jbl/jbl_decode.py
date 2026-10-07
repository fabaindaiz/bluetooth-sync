#!/usr/bin/env python3
"""Decode what the JBL speakers advertise, from a sniffer or HCI capture file (experiment 22).

Throwaway probe (d-7c8794-3208b7): deleted once its result is written in
docs/research/experimentos/. It never opens a serial port or a radio: it only reads a file.

Inputs (detected from the file header):
  * classic pcap, DLT 272 (LINKTYPE_NORDIC_BLE): nRF Sniffer for Bluetooth LE 4.x
  * classic pcap, DLT 201 (H4 with a 4-byte direction header): Bumble's `pcapsnoop`
  * classic pcap, DLT 187 (H4)
  * btsnoop, datalink 1002 (H4): Bumble's `btsnoop` snooper, Android's HCI snoop log
A pcapng file (what Wireshark saves by default) is converted first:
    editcap -F pcap captura.pcapng captura.pcap

Output: one JSON object per line (events), then one `summary` object per advertiser.
Usage:
    python jbl_decode.py captura.pcap [--all] > eventos.jsonl

Sources for the formats: Core 5.4 Vol 6 Part B 2.3 (advertising PDUs) and 4.4.2.11 (BIGInfo,
field masks cross-checked with Wireshark's packet-bthci_cmd.c); Vol 4 Part E 7.7.65 (HCI LE meta
events); BAP 1.0 3.7.2.2 (BASE); LINKTYPE_NORDIC_BLE (tcpdump.org) and the nRF Sniffer 4.1.1 UART
protocol document; the JBL fields from docs/research/experimentos/01-e2-anuncios-jbl-mac.md.
"""

from __future__ import annotations

import argparse
import json
import statistics
import struct
import sys
from collections import defaultdict
from collections.abc import Iterator

HARMAN_COMPANY_ID = 0x0057
JBL_BROADCAST_TAG = bytes(16) + b'\xdf\xfd'  # MEASURED, experiment 01
JBL_MODELS = {0x20E4: 'Go 4', 0x20E3: 'Charge 6'}
UUID_BASIC_AUDIO_ANNOUNCEMENT = 0x1851  # carries the BASE
UUID_BROADCAST_AUDIO_ANNOUNCEMENT = 0x1852  # Broadcast_ID
UUID_PUBLIC_BROADCAST_ANNOUNCEMENT = 0x1856  # PBP
ADV_ACCESS_ADDRESS = 0x8E89BED6

SAMPLING_FREQUENCIES = {1: 8000, 2: 11025, 3: 16000, 4: 22050, 5: 24000, 6: 32000,
                        7: 44100, 8: 48000, 9: 88200, 10: 96000, 11: 176400, 12: 192000,
                        13: 384000}
AUDIO_LOCATIONS = ['FL', 'FR', 'FC', 'LFE1', 'BL', 'BR', 'FLC', 'FRC', 'BC', 'LFE2', 'SIL',
                   'SIR', 'TFL', 'TFR', 'TFC', 'TC', 'TBL', 'TBR', 'TSL', 'TSR', 'TBC', 'BFC',
                   'BFL', 'BFR', 'FLW', 'FRW', 'LS', 'RS']
LEGACY_PDU = {0: 'ADV_IND', 1: 'ADV_DIRECT_IND', 2: 'ADV_NONCONN_IND', 3: 'SCAN_REQ',
              4: 'SCAN_RSP', 5: 'CONNECT_IND', 6: 'ADV_SCAN_IND'}
AUX_TYPES = {0: 'AUX_ADV_IND', 1: 'AUX_CHAIN_IND', 2: 'AUX_SYNC_IND', 3: 'AUX_SCAN_RSP'}
BTSNOOP_EPOCH_DELTA_US = 0x00DCDDB30F2F8000  # btsnoop time is in us since year 0
# Fields that change on every BIGInfo; left out when grouping distinct BIGInfo in the summary.
VOLATILE = ('bis_payload_count', 'big_offset', 'big_offset_us', 'big_anchor_fw_us')


# ---------------------------------------------------------------------------------------------
# Readers: (time in seconds, 'nordic' | 'h4', bytes)


def read_capture(path: str) -> Iterator[tuple[float, str, bytes]]:
    with open(path, 'rb') as f:
        raw = f.read()
    if raw[:8] == b'btsnoop\0':
        _version, datalink = struct.unpack_from('>II', raw, 8)
        if datalink != 1002:
            raise SystemExit(f'btsnoop datalink {datalink} not supported (only 1002, H4)')
        off = 16
        while off + 24 <= len(raw):
            _orig, incl, _flags, _drops, ts = struct.unpack_from('>IIIIq', raw, off)
            off += 24
            yield (ts - BTSNOOP_EPOCH_DELTA_US) / 1e6, 'h4', raw[off:off + incl]
            off += incl
        return
    magic = raw[:4]
    if magic in (b'\xd4\xc3\xb2\xa1', b'\x4d\x3c\xb2\xa1'):
        end, nano = '<', magic == b'\x4d\x3c\xb2\xa1'
    elif magic in (b'\xa1\xb2\xc3\xd4', b'\xa1\xb2\x3c\x4d'):
        end, nano = '>', magic == b'\xa1\xb2\x3c\x4d'
    elif magic == b'\x0a\x0d\x0d\x0a':
        raise SystemExit('pcapng: convert it first with `editcap -F pcap in.pcapng out.pcap`')
    else:
        raise SystemExit('unknown capture format')
    linktype = struct.unpack_from(end + 'I', raw, 20)[0] & 0x0FFFFFFF
    off = 24
    while off + 16 <= len(raw):
        sec, frac, incl, _orig = struct.unpack_from(end + 'IIII', raw, off)
        off += 16
        rec = raw[off:off + incl]
        off += incl
        t = sec + frac / (1e9 if nano else 1e6)
        if linktype == 272:
            yield t, 'nordic', rec
        elif linktype == 201:
            yield t, 'h4', rec[4:]
        elif linktype == 187:
            yield t, 'h4', rec
        else:
            raise SystemExit(f'pcap linktype {linktype} not supported')


# ---------------------------------------------------------------------------------------------
# Advertising data


def parse_ad(data: bytes) -> list[tuple[int, bytes]]:
    """AD structures as (type, value). Stops at a zero length or a truncated entry."""
    out, i = [], 0
    while i < len(data):
        n = data[i]
        if n == 0 or i + 1 + n > len(data):
            break
        out.append((data[i + 1], bytes(data[i + 2:i + 1 + n])))
        i += 1 + n
    return out


def decode_jbl_manufacturer(payload: bytes) -> dict:
    """Harman (0x0057) manufacturer data, without the company id.

    The field names are hypotheses from experiment 01 (INFERRED there), so the raw bytes always
    go along. A change of any byte is what the caller tracks.
    """
    if payload == JBL_BROADCAST_TAG:
        return {'format': 'broadcast_tag', 'hex': payload.hex()}
    out: dict = {'format': f'{len(payload)}B', 'hex': payload.hex()}
    if len(payload) == 10:
        model = int.from_bytes(payload[0:2], 'little')
        out.update({
            'model_id': f'0x{model:04X}',
            'model': JBL_MODELS.get(model),
            'byte2_color_hypothesis': payload[2],
            'byte3': f'0x{payload[3]:02x}',
            'unit_bytes4_7': payload[4:8].hex(),
            'byte8': f'0x{payload[8]:02x}',
            'byte9': f'0x{payload[9]:02x}',
            # Hypotheses of experiment 01: byte3 bit0 and byte8 bit1 when broadcasting (Charge 6),
            # byte9 = 0x60 in a stereo group (two Go 4). INFERRED, never confirmed.
            'hyp_broadcasting': bool(payload[3] & 0x01) or bool(payload[8] & 0x02),
            'hyp_stereo_group': payload[9] & 0x60 == 0x60,
        })
    return out


decode_ltv = parse_ad  # BAP's length-type-value lists have the same layout as AD structures


def decode_codec_config(data: bytes) -> dict:
    out: dict = {}
    for t, v in decode_ltv(data):
        if t == 1 and len(v) == 1:
            out['sampling_hz'] = SAMPLING_FREQUENCIES.get(v[0], f'code {v[0]}')
        elif t == 2 and len(v) == 1:
            out['frame_ms'] = {0: 7.5, 1: 10.0}.get(v[0], f'code {v[0]}')
        elif t == 3 and len(v) == 4:
            mask = int.from_bytes(v, 'little')
            out['allocation'] = f'0x{mask:08x}'
            out['locations'] = [n for b, n in enumerate(AUDIO_LOCATIONS) if mask >> b & 1] or ['mono']
        elif t == 4 and len(v) == 2:
            out['octets_per_frame'] = int.from_bytes(v, 'little')
        elif t == 5 and len(v) == 1:
            out['frame_blocks_per_sdu'] = v[0]
        else:
            out[f'ltv_0x{t:02x}'] = v.hex()
    return out


def decode_metadata(data: bytes) -> dict:
    out: dict = {}
    for t, v in decode_ltv(data):
        if t in (0x01, 0x02) and len(v) == 2:  # preferred / streaming audio contexts
            out['contexts' if t == 2 else 'preferred_contexts'] = f'0x{int.from_bytes(v, "little"):04x}'
        elif t in (0x03, 0x04):  # program info, language
            out['program_info' if t == 3 else 'language'] = v.decode('utf-8', 'replace')
        else:
            out[f'ltv_0x{t:02x}'] = v.hex()
    return out


def decode_base(data: bytes) -> dict:
    """BAP 3.7.2.2. Lenient: returns what it could parse and an `error` if it stopped early."""
    out: dict = {'hex': data.hex(), 'subgroups': []}
    try:
        out['presentation_delay_us'] = int.from_bytes(data[0:3], 'little')
        n_sub, off = data[3], 4
        for _ in range(n_sub):
            n_bis = data[off]
            codec = data[off + 1:off + 6]
            off += 6
            cfg_len = data[off]
            cfg = decode_codec_config(data[off + 1:off + 1 + cfg_len])
            off += 1 + cfg_len
            meta_len = data[off]
            meta = decode_metadata(data[off + 1:off + 1 + meta_len])
            off += 1 + meta_len
            sub = {'codec_id': codec.hex(), 'lc3': codec[0] == 0x06, 'config': cfg,
                   'metadata': meta, 'bis': []}
            for _ in range(n_bis):
                idx, bis_len = data[off], data[off + 1]
                sub['bis'].append({'index': idx,
                                   'config': decode_codec_config(data[off + 2:off + 2 + bis_len])})
                off += 2 + bis_len
            out['subgroups'].append(sub)
        if off != len(data):
            out['trailing'] = data[off:].hex()
    except IndexError:
        out['error'] = 'truncated'
    return out


def decode_biginfo(v: bytes) -> dict:
    """BIGInfo AD (type 0x2C, in ACAD). Masks as in Wireshark's packet-bthci_cmd.c."""
    if len(v) not in (33, 57):
        return {'error': f'length {len(v)}', 'hex': v.hex()}
    w0, w1 = struct.unpack_from('<II', v, 0)
    w2 = int.from_bytes(v[8:11], 'little')
    w3 = struct.unpack_from('<I', v, 17)[0]
    w4 = int.from_bytes(v[23:28], 'little')
    w5 = int.from_bytes(v[28:33], 'little')
    units = w0 >> 14 & 1
    out = {
        'big_offset': w0 & 0x3FFF, 'big_offset_units_us': 300 if units else 30,
        'iso_interval_ms': (w0 >> 15 & 0xFFF) * 1.25, 'num_bis': w0 >> 27 & 0x1F,
        'nse': w1 & 0x1F, 'bn': w1 >> 5 & 0x7, 'sub_interval_us': w1 >> 8 & 0xFFFFF,
        'pto': w1 >> 28 & 0xF, 'bis_spacing_us': w2 & 0xFFFFF, 'irc': w2 >> 20 & 0xF,
        'max_pdu': v[11], 'seed_access_address': f'0x{struct.unpack_from("<I", v, 13)[0]:08x}',
        'sdu_interval_us': w3 & 0xFFFFF, 'max_sdu': w3 >> 20 & 0xFFF,
        'base_crc_init': f'0x{int.from_bytes(v[21:23], "little"):04x}',
        'channel_map': f'0x{w4 & 0x1FFFFFFFFF:010x}',
        'phy': {0: '1M', 1: '2M', 2: 'Coded'}.get(w4 >> 37 & 0x7, w4 >> 37 & 0x7),
        'bis_payload_count': w5 & 0x7FFFFFFFFF, 'framed': bool(w5 >> 39 & 1),
        'encrypted': len(v) == 57,
    }
    out['big_offset_us'] = out['big_offset'] * out['big_offset_units_us']
    return out


def decode_ad_list(ads: list[tuple[int, bytes]]) -> dict:
    """The fields this probe cares about, plus the AD types seen."""
    out: dict = {'ad_types': sorted({f'0x{t:02x}' for t, _ in ads})}
    for t, v in ads:
        if t in (0x08, 0x09):
            out['name'] = v.decode('utf-8', 'replace')
        elif t == 0x30:
            out['broadcast_name'] = v.decode('utf-8', 'replace')
        elif t == 0xFF and len(v) >= 2:
            cid = int.from_bytes(v[:2], 'little')
            if cid == HARMAN_COMPANY_ID:
                out['jbl'] = decode_jbl_manufacturer(v[2:])
            else:
                out.setdefault('manufacturer', []).append({'company': f'0x{cid:04x}', 'hex': v[2:].hex()})
        elif t == 0x16 and len(v) >= 2:
            uuid, sd = int.from_bytes(v[:2], 'little'), v[2:]
            if uuid == UUID_BROADCAST_AUDIO_ANNOUNCEMENT and len(sd) >= 3:
                out['broadcast_id'] = f'0x{int.from_bytes(sd[:3], "little"):06x}'
            elif uuid == UUID_BASIC_AUDIO_ANNOUNCEMENT:
                out['base'] = decode_base(sd)
            elif uuid == UUID_PUBLIC_BROADCAST_ANNOUNCEMENT and len(sd) >= 2:
                out['pbp'] = {'features': f'0x{sd[0]:02x}', 'encrypted': bool(sd[0] & 1),
                              'standard_quality': bool(sd[0] & 2), 'high_quality': bool(sd[0] & 4),
                              'metadata': decode_metadata(sd[2:2 + sd[1]])}
            else:
                out.setdefault('service_data', {})[f'0x{uuid:04x}'] = sd.hex()
        elif t == 0x2C:
            out['biginfo'] = decode_biginfo(v)
    return out


# ---------------------------------------------------------------------------------------------
# Link layer (nRF Sniffer, DLT 272)


def fmt_addr(b: bytes) -> str:
    return ':'.join(f'{x:02X}' for x in reversed(b))


def parse_nordic(rec: bytes) -> dict | None:
    """One LINKTYPE_NORDIC_BLE record -> metadata and the LL PDU (no preamble, no S1 padding)."""
    if len(rec) < 7 + 10 + 4 + 2:
        return None
    packet_id = rec[6]
    if packet_id not in (0x02, 0x06):
        return None
    hlen = rec[7]
    flags, channel, rssi = rec[8], rec[9], rec[10]
    fw_ts = struct.unpack_from('<I', rec, 13)[0]
    ll = rec[7 + hlen:]
    phy = flags >> 4 & 0x7
    aa = struct.unpack_from('<I', ll, 0)[0]
    pdu_start = 5 if phy == 2 else 4  # coded PHY carries a coding indicator byte
    if len(ll) < pdu_start + 2:
        return None
    hdr, length = ll[pdu_start], ll[pdu_start + 1]
    payload = ll[pdu_start + 2:pdu_start + 2 + length]
    return {'adv': packet_id == 0x02, 'channel': channel, 'rssi': -rssi,
            'crc_ok': bool(flags & 1), 'aux_type': flags >> 1 & 0x3, 'phy': phy,
            'fw_ts_us': fw_ts, 'aa': aa, 'pdu_type': hdr & 0xF, 'tx_add': hdr >> 6 & 1,
            'payload': payload}


def parse_extended(payload: bytes) -> dict:
    """Common Extended Advertising Payload Format (Core Vol 6 Part B 2.3.4)."""
    out: dict = {}
    if not payload:
        return out
    hlen, out['adv_mode'] = payload[0] & 0x3F, payload[0] >> 6
    hdr = payload[1:1 + hlen]
    out['adv_data'] = payload[1 + hlen:]
    if hlen == 0:
        return out
    flags, i = hdr[0], 1
    if flags & 0x01:
        out['adv_a'] = fmt_addr(hdr[i:i + 6])
        i += 6
    if flags & 0x02:
        out['target_a'] = fmt_addr(hdr[i:i + 6])
        i += 6
    if flags & 0x04:
        i += 1
    if flags & 0x08:
        adi = int.from_bytes(hdr[i:i + 2], 'little')
        out['did'], out['sid'] = adi & 0xFFF, adi >> 12
        i += 2
    if flags & 0x10:
        ap = int.from_bytes(hdr[i:i + 3], 'little')
        out['aux_ptr'] = {'channel': ap & 0x3F, 'offset_us': (ap >> 8 & 0x1FFF) * (300 if ap >> 7 & 1 else 30),
                          'phy': ap >> 21 & 0x7}
        i += 3
    if flags & 0x20:
        si = hdr[i:i + 18]
        w = int.from_bytes(si[0:2], 'little')
        out['sync_info'] = {
            'offset_us': (w & 0x1FFF) * (300 if w >> 13 & 1 else 30) + (2457600 if w >> 14 & 1 else 0),
            'interval_ms': int.from_bytes(si[2:4], 'little') * 1.25,
            'sca': si[8] >> 5, 'aa': struct.unpack_from('<I', si, 9)[0],
            'event_counter': int.from_bytes(si[16:18], 'little')}
        i += 18
    if flags & 0x40:
        out['tx_power'] = struct.unpack_from('b', hdr, i)[0]
        i += 1
    out['acad'] = hdr[i:]
    return out


# ---------------------------------------------------------------------------------------------
# The decoder: turns packets into events and keeps per-advertiser state for the summary


class Decoder:
    def __init__(self, show_all: bool):
        self.show_all = show_all
        self.adv: dict[str, dict] = defaultdict(lambda: {'first': None, 'last': None, 'pdus': defaultdict(int)})
        self.pa_aa: dict[int, str] = {}  # periodic AA (from SyncInfo) -> advertiser
        self.sync_handles: dict[int, str] = {}  # HCI sync handle -> advertiser
        self.frag: dict = {}
        self.iso: dict[int, dict] = {}

    def emit(self, ev: dict) -> None:
        print(json.dumps(ev, ensure_ascii=False, default=str))

    def note(self, t: float, who: str, kind: str, fields: dict, extra: dict | None = None) -> None:
        s = self.adv[who]
        s['first'] = t if s['first'] is None else s['first']
        s['last'] = t
        s['pdus'][kind] += 1
        interesting = any(k in fields for k in ('jbl', 'broadcast_id', 'base', 'pbp', 'biginfo', 'broadcast_name'))
        if 'jbl' in fields:
            prev = s.get('jbl_hex')
            if prev != fields['jbl']['hex']:
                self.emit({'t': t, 'event': 'jbl_change', 'who': who, 'from': prev, 'to': fields['jbl']})
                s['jbl_hex'] = fields['jbl']['hex']
            s.setdefault('jbl_variants', {}).setdefault(fields['jbl']['hex'], [t, t])[1] = t
        for key in ('name', 'broadcast_name', 'broadcast_id'):
            if key in fields:
                s.setdefault(key + 's', set()).add(fields[key])
        for key in ('base', 'pbp', 'biginfo'):
            if key in fields:
                core = {k: v for k, v in fields[key].items() if k not in VOLATILE}
                s.setdefault(key, {})[json.dumps(core, sort_keys=True, default=str)] = core
        if interesting or self.show_all:
            self.emit({'t': t, 'event': kind, 'who': who, **(extra or {}), **fields})

    # -- nRF Sniffer
    def nordic(self, t: float, rec: bytes) -> None:
        p = parse_nordic(rec)
        if p is None or not p['adv'] or not p['crc_ok']:
            return
        pl, primary = p['payload'], p['channel'] >= 37
        meta = {'ch': p['channel'], 'rssi': p['rssi'], 'fw_ts_us': p['fw_ts_us']}
        if p['pdu_type'] == 5 and len(pl) >= 34:  # CONNECT_IND, or AUX_CONNECT_REQ off 37-39
            init, adv = fmt_addr(pl[0:6]), fmt_addr(pl[6:12])
            ll = pl[12:]
            self.emit({'t': t, 'event': 'CONNECT_IND' if primary else 'AUX_CONNECT_REQ',
                       'initiator': init, 'advertiser': adv,
                       'access_address': f'0x{struct.unpack_from("<I", ll, 0)[0]:08x}',
                       'interval_ms': int.from_bytes(ll[10:12], 'little') * 1.25, **meta})
            self.adv[adv]['pdus']['CONNECT_IND'] += 1
            return
        if p['pdu_type'] != 7:
            if p['pdu_type'] in (0, 2, 4, 6) and len(pl) >= 6:
                self.note(t, fmt_addr(pl[0:6]), LEGACY_PDU[p['pdu_type']], decode_ad_list(parse_ad(pl[6:])), meta)
            return
        ext = parse_extended(pl)
        kind = 'ADV_EXT_IND' if primary else AUX_TYPES[p['aux_type']]
        who = ext.get('adv_a') or self.pa_aa.get(p['aa']) or f'aa:0x{p["aa"]:08x}'
        if 'sync_info' in ext:
            self.pa_aa[ext['sync_info']['aa']] = who
            meta['sync_info'] = ext['sync_info']
        if 'sid' in ext:
            meta['sid'] = ext['sid']
        fields = decode_ad_list(parse_ad(ext.get('adv_data', b'')) + parse_ad(ext.get('acad', b'')))
        if kind == 'AUX_SYNC_IND':
            self.adv[who].setdefault('sync_ts_us', []).append(p['fw_ts_us'])
            if 'biginfo' in fields:  # next BIG anchor, in the sniffer's clock (INFERRED reading)
                fields['biginfo']['big_anchor_fw_us'] = p['fw_ts_us'] + fields['biginfo']['big_offset_us']
        self.note(t, who, kind, fields, meta)

    # -- HCI (Bumble snoop, Android snoop)
    def h4(self, t: float, pkt: bytes) -> None:
        if not pkt:
            return
        if pkt[0] == 0x05 and len(pkt) >= 5:
            self.iso_packet(t, pkt[1:])
            return
        if pkt[0] != 0x04 or len(pkt) < 4 or pkt[1] != 0x3E:
            return
        sub, p = pkt[3], pkt[4:]
        if sub == 0x0D:  # LE Extended Advertising Report
            n, off = p[0], 1
            for _ in range(n):
                etype = int.from_bytes(p[off:off + 2], 'little')
                who = fmt_addr(p[off + 3:off + 9])
                sid, rssi = p[off + 11], struct.unpack_from('b', p, off + 13)[0]
                pa_int = int.from_bytes(p[off + 14:off + 16], 'little')
                dlen = p[off + 23]
                data = p[off + 24:off + 24 + dlen]
                off += 24 + dlen
                key = ('ext', who, sid)
                data = self.frag.pop(key, b'') + data
                if etype >> 5 & 0x3 == 1:
                    self.frag[key] = data
                    continue
                kind = 'HCI_ext_report' + ('_legacy' if etype & 0x10 else '')
                meta = {'rssi': rssi, 'sid': sid}
                if pa_int:
                    meta['pa_interval_ms'] = pa_int * 1.25
                self.note(t, who, kind, decode_ad_list(parse_ad(data)), meta)
        elif sub in (0x0E, 0x24):  # Periodic Advertising Sync Established (v1, v2)
            status, handle = p[0], int.from_bytes(p[1:3], 'little')
            who = fmt_addr(p[5:11])
            if status == 0:
                self.sync_handles[handle] = who
            self.emit({'t': t, 'event': 'pa_sync_established', 'who': who, 'status': status,
                       'sync_handle': handle, 'sid': p[3],
                       'interval_ms': int.from_bytes(p[12:14], 'little') * 1.25})
        elif sub in (0x0F, 0x25):  # Periodic Advertising Report (v1, v2)
            handle = int.from_bytes(p[0:2], 'little')
            status_at, len_at = (5, 6) if sub == 0x0F else (8, 9)
            status, dlen = p[status_at], p[len_at]
            data = self.frag.pop(('pa', handle), b'') + p[len_at + 1:len_at + 1 + dlen]
            if status == 1:
                self.frag[('pa', handle)] = data
                return
            who = self.sync_handles.get(handle, f'sync:{handle}')
            self.adv[who].setdefault('pa_t', []).append(t)
            self.note(t, who, 'HCI_pa_report', decode_ad_list(parse_ad(data)),
                      {'rssi': struct.unpack_from('b', p, 3)[0]})
        elif sub == 0x22:  # BIGInfo Advertising Report
            handle = int.from_bytes(p[0:2], 'little')
            bi = {'num_bis': p[2], 'nse': p[3], 'iso_interval_ms': int.from_bytes(p[4:6], 'little') * 1.25,
                  'bn': p[6], 'pto': p[7], 'irc': p[8], 'max_pdu': int.from_bytes(p[9:11], 'little'),
                  'sdu_interval_us': int.from_bytes(p[11:14], 'little'),
                  'max_sdu': int.from_bytes(p[14:16], 'little'),
                  'phy': {1: '1M', 2: '2M', 3: 'Coded'}.get(p[16], p[16]), 'framed': bool(p[17]),
                  'encrypted': bool(p[18])}
            self.note(t, self.sync_handles.get(handle, f'sync:{handle}'), 'HCI_biginfo', {'biginfo': bi})
        elif sub == 0x1D:  # BIG Sync Established
            n_bis = p[13] if len(p) > 13 else 0
            self.emit({'t': t, 'event': 'big_sync_established', 'status': p[0], 'big_handle': p[1],
                       'transport_latency_us': int.from_bytes(p[2:5], 'little'), 'nse': p[5], 'bn': p[6],
                       'pto': p[7], 'irc': p[8], 'max_pdu': int.from_bytes(p[9:11], 'little'),
                       'iso_interval_ms': int.from_bytes(p[11:13], 'little') * 1.25,
                       'bis_handles': [int.from_bytes(p[14 + 2 * k:16 + 2 * k], 'little') for k in range(n_bis)]})

    def iso_packet(self, t: float, p: bytes) -> None:
        """HCI ISO data from the controller: counts, sequence gaps, SDU timestamps per BIS."""
        w = int.from_bytes(p[0:2], 'little')
        handle, has_ts = w & 0x0FFF, bool(w >> 14 & 1)
        off = 4
        ts = None
        if has_ts:
            ts = struct.unpack_from('<I', p, off)[0]
            off += 4
        if w >> 12 & 0x3 not in (0x2, 0x0) or len(p) < off + 4:
            return
        psn = int.from_bytes(p[off:off + 2], 'little')
        s = self.iso.setdefault(handle, {'sdus': 0, 'gaps': 0, 'last_psn': None, 'ts': []})
        s['sdus'] += 1
        if s['last_psn'] is not None and (psn - s['last_psn']) & 0xFFFF != 1:
            s['gaps'] += 1
        s['last_psn'] = psn
        if ts is not None:
            s['ts'].append(ts)

    def summary(self) -> None:
        for who, s in self.adv.items():
            out: dict = {'event': 'summary', 'who': who, 'first': s['first'], 'last': s['last'],
                         'pdus': dict(s['pdus'])}
            for key in ('names', 'broadcast_names', 'broadcast_ids'):
                if key in s:
                    out[key] = sorted(s[key])
            for key in ('jbl_variants',):
                if key in s:
                    out[key] = s[key]
            for key in ('base', 'pbp', 'biginfo'):
                if key in s:
                    out[key] = list(s[key].values())
            ts = s.get('sync_ts_us', [])
            if len(ts) > 2:  # PA interval as the sniffer measures it: the JBL clock vs the sniffer's
                d = [(b - a) & 0xFFFFFFFF for a, b in zip(ts, ts[1:])]
                out['aux_sync_interval_us'] = {'median': statistics.median(d), 'min': min(d), 'max': max(d), 'n': len(d)}
            if len(out['pdus']) or self.show_all:
                print(json.dumps(out, ensure_ascii=False, default=str))
        for handle, s in self.iso.items():
            d = [(b - a) & 0xFFFFFFFF for a, b in zip(s['ts'], s['ts'][1:])]
            print(json.dumps({'event': 'summary_iso', 'handle': handle, 'sdus': s['sdus'], 'psn_gaps': s['gaps'],
                              'sdu_interval_us_median': statistics.median(d) if d else None}))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('capture')
    ap.add_argument('--all', action='store_true', help='also print PDUs without JBL/Auracast fields')
    args = ap.parse_args()
    dec = Decoder(args.all)
    for t, kind, data in read_capture(args.capture):
        try:
            dec.nordic(t, data) if kind == 'nordic' else dec.h4(t, data)
        except (IndexError, struct.error) as e:  # a malformed packet must not stop the run
            print(json.dumps({'t': t, 'event': 'malformed', 'error': repr(e), 'hex': data.hex()}))
    dec.summary()


if __name__ == '__main__':
    sys.exit(main())
