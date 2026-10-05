"""The connection code (one short code that carries the device's address, its pairing code and part
of its root's fingerprint): typed once in the PWA, it finds the device, checks it is the right one
and pairs. The same vectors are in web/test/code.test.ts: both sides must agree."""

import pytest

from aurasync import connection_code as cc

FP = "F70C8B6BD81B17BEC942A838C84F9776956EBE60AD95BDF1CBA01D40E32B8E5B"

# (host, port, pairing code) -> code; shared with web/test/code.test.ts.
VECTORS = [
    ("192.168.100.11", 8443, "042137"),
    ("10.241.98.125", 8443, "999999"),
    ("172.20.0.5", 9443, "000000"),
    ("8.8.4.4", 8443, "123456"),
]


@pytest.mark.parametrize(("host", "port", "pairing"), VECTORS)
def test_a_code_decodes_to_what_was_encoded(host, port, pairing):
    code = cc.encode(host, port, pairing, FP)
    got = cc.decode(code)
    assert got == cc.Decoded(host, port, pairing, FP[: cc.FP_HEX])


def test_a_home_network_code_is_short_and_grouped():
    code = cc.encode("192.168.100.11", 8443, "042137", FP)
    assert len(code.replace("-", "")) == 13
    assert all(len(g) <= 4 for g in code.split("-"))
    assert set(code.replace("-", "")) <= set(cc.ALPHABET)


def test_typing_is_forgiving_but_a_typo_is_caught():
    code = cc.encode("192.168.100.11", 8443, "042137", FP)
    sloppy = code.lower().replace("-", " ").replace("0", "o").replace("1", "l")
    assert cc.decode(sloppy) == cc.decode(code)
    plain = code.replace("-", "")
    wrong = plain[:3] + ("A" if plain[3] != "A" else "B") + plain[4:]
    with pytest.raises(cc.CodeError):
        cc.decode(wrong)


@pytest.mark.parametrize("bad", ["", "XYZ", "aurasync.local:8443", "1234-5678"])
def test_what_is_not_a_code_says_so(bad):
    with pytest.raises(cc.CodeError):
        cc.decode(bad)


def test_only_ipv4_and_a_six_digit_code_are_encoded():
    with pytest.raises(ValueError, match="IPv4"):
        cc.encode("aurasync.local", 8443, "042137", FP)
    with pytest.raises(ValueError, match="6 digits"):
        cc.encode("192.168.1.2", 8443, "42", FP)


def test_the_shared_vectors_are_stable():
    """If this changes, web/test/code.test.ts must change with it (and old codes stop working)."""
    assert [cc.encode(h, p, c, FP) for h, p, c in VECTORS] == CODES


CODES = ["341C-2J9K-XRCG-B", "FHC9-YQM4-FZQ1-J06", "J001-CJE6-0001-XRCG-9", "R810-2083-RJ0Y-W680"]
