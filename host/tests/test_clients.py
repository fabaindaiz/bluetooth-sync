"""Clients, scopes, pairing, failed attempts and stream tickets, without HTTP."""

import json
import os
import stat

import pytest

from aurasync import access as access_module
from aurasync import clients as clients_module
from aurasync import control, pairing
from aurasync.access import Access, AttemptLimiter, StreamTickets
from aurasync.clients import ClientStore, Principal, required_scope
from aurasync.control import ContractError
from aurasync.pairing import PairingDesk


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# -- the store ---------------------------------------------------------------------------


def test_the_file_keeps_only_a_salted_hash(tmp_path):
    store = ClientStore(tmp_path / "clients.json")
    client, token = store.add("Pixel", "control", "192.168.1.9")
    assert token.startswith(f"asc_{client.id}_")
    path = tmp_path / "clients.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    secret = token.split("_", 2)[2]
    assert secret not in text
    saved = json.loads(text)["clients"][0]
    assert saved["hash"] != secret
    assert len(saved["salt"]) == 32
    assert ClientStore(path).verify(token) == Principal(client.id, "Pixel", "control")


def test_wrong_unknown_and_malformed_tokens_do_not_verify(tmp_path):
    store = ClientStore(tmp_path / "c.json")
    client, token = store.add("a", "read")
    assert store.verify(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert store.verify(f"asc_00000000_{token.split('_', 2)[2]}") is None
    assert store.verify("nope") is None
    assert store.verify(token).id == client.id


def test_revoke_and_rename(tmp_path):
    store = ClientStore(tmp_path / "c.json")
    client, token = store.add("old", "control")
    store.rename(client.id, "new")
    assert ClientStore(tmp_path / "c.json").list()[0]["name"] == "new"
    store.revoke(client.id)
    assert store.verify(token) is None
    assert ClientStore(tmp_path / "c.json").verify(token) is None, "the revocation reached the file"
    with pytest.raises(ContractError) as exc:
        store.revoke(client.id)
    assert exc.value.code == "not_found"


def test_rotating_the_master_token_disconnects_no_client(tmp_path):
    """Card secrets-survive-rotation: rotate the secret; nothing stored stops verifying."""
    path = tmp_path / "c.json"
    before = Access("A" * 43, ClientStore(path))
    _, token = before.store.add("phone", "control")
    assert before.verify("A" * 43).is_master
    after = Access("B" * 43, ClientStore(path))
    assert after.verify(token) is not None
    assert after.verify("A" * 43) is None
    assert after.verify("B" * 43).is_master


def test_a_client_unused_for_too_long_expires(tmp_path):
    clock = Clock(1_000_000.0)
    store = ClientStore(tmp_path / "c.json", clock=clock)
    _, token = store.add("phone", "read")
    clock.t += (clients_module.IDLE_DAYS - 1) * 86400
    assert store.verify(token) is not None, "used: the clock restarts"
    clock.t += (clients_module.IDLE_DAYS + 1) * 86400
    assert store.verify(token) is None


def test_last_used_reaches_the_file_at_most_once_a_minute(tmp_path):
    clock = Clock(1_000_000.0)
    store = ClientStore(tmp_path / "c.json", clock=clock)
    _, token = store.add("phone", "read")
    clock.t += 5
    store.verify(token)
    assert json.loads((tmp_path / "c.json").read_text())["clients"][0]["last_used"] == 1_000_000.0
    clock.t += clients_module.TOUCH_WRITE_S
    store.verify(token)
    assert json.loads((tmp_path / "c.json").read_text())["clients"][0]["last_used"] == clock.t
    clock.t += 1
    store.verify(token)
    store.flush()
    assert json.loads((tmp_path / "c.json").read_text())["clients"][0]["last_used"] == clock.t


def test_a_readable_file_refuses(tmp_path):
    path = tmp_path / "c.json"
    ClientStore(path)
    os.chmod(path, 0o644)
    with pytest.raises(PermissionError, match="chmod 600"):
        ClientStore(path)


# -- scopes ------------------------------------------------------------------------------


def test_every_operation_of_the_contract_has_a_scope():
    classified = clients_module.READ_OPS | clients_module.CONTROL_OPS | clients_module.ADMIN_OPS
    assert set(control.OPS) <= classified, set(control.OPS) - classified


def test_an_unclassified_operation_needs_admin():
    assert required_scope("state") == "read"
    assert required_scope("set") == "control"
    assert required_scope("shutdown") == "admin"
    assert required_scope("some_new_op") == "admin"
    assert required_scope(None) == "admin"


# -- the limiter -------------------------------------------------------------------------


def test_failures_block_after_the_free_ones_and_the_block_doubles():
    clock = Clock()
    limiter = AttemptLimiter(clock)
    for _ in range(access_module.FREE_FAILURES):
        limiter.fail("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") == 0
    limiter.fail("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") == pytest.approx(1.0)
    limiter.fail("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") == pytest.approx(2.0)
    assert limiter.blocked_for("5.6.7.8") == 0
    for _ in range(30):
        limiter.fail("1.2.3.4")
    assert limiter.blocked_for("1.2.3.4") == pytest.approx(access_module.MAX_BLOCK_S)


def test_a_success_does_not_lift_a_block_that_is_running():
    clock = Clock()
    limiter = AttemptLimiter(clock)
    for _ in range(access_module.FREE_FAILURES + 1):
        limiter.fail("ip")
    limiter.succeed("ip")
    assert limiter.blocked_for("ip") > 0


def test_a_full_table_still_blocks():
    """Card abuser-controlled-exemption: just above the cap, the control still fires."""
    clock = Clock()
    size = 8
    limiter = AttemptLimiter(clock, size=size)
    for i in range(size):
        for _ in range(access_module.FREE_FAILURES + 1):
            limiter.fail(f"10.0.0.{i}")
    assert all(limiter.blocked_for(f"10.0.0.{i}") > 0 for i in range(size)), "nobody was evicted"
    newcomer = "10.0.0.200"
    for _ in range(access_module.FREE_FAILURES + 1):
        limiter.fail(newcomer)
    assert limiter.blocked_for(newcomer) > 0
    assert all(limiter.blocked_for(f"10.0.0.{i}") > 0 for i in range(size)), "a newcomer let a blocked address out"


def test_a_full_table_forgets_the_addresses_that_are_not_blocked_first():
    clock = Clock()
    limiter = AttemptLimiter(clock, size=2)
    for _ in range(access_module.FREE_FAILURES + 1):
        limiter.fail("bad")
    limiter.fail("harmless")
    limiter.fail("third")
    assert limiter.blocked_for("bad") > 0


# -- stream tickets ----------------------------------------------------------------------


def test_a_ticket_works_once_and_not_after_it_expires():
    clock = Clock()
    tickets = StreamTickets(clock)
    who = Principal("abcd1234", "phone", "read")
    first = tickets.issue(who)["ticket"]
    assert tickets.redeem(first) == who
    assert tickets.redeem(first) is None
    late = tickets.issue(who)["ticket"]
    clock.t += access_module.TICKET_TTL_S + 0.1
    assert tickets.redeem(late) is None
    assert tickets.redeem("made-up") is None


def test_unused_tickets_are_capped():
    clock = Clock()
    tickets = StreamTickets(clock)
    who = Principal("abcd1234", "phone", "read")
    for _ in range(access_module.MAX_TICKETS):
        tickets.issue(who)
    with pytest.raises(ContractError) as exc:
        tickets.issue(who)
    assert exc.value.code == "busy"
    clock.t += access_module.TICKET_TTL_S + 1
    tickets.issue(who)


# -- pairing -----------------------------------------------------------------------------


def desk(tmp_path, window_s=600.0):
    clock = Clock()
    shown = []
    store = ClientStore(tmp_path / "c.json")
    return PairingDesk(store, window_s=window_s, clock=clock, show_code=lambda c, _s: shown.append(c)), clock, shown


def test_the_first_request_in_the_window_is_approved_as_admin_and_only_the_first(tmp_path):
    d, _, _ = desk(tmp_path)
    assert d.status()["window"]["open"]
    view, _ = d.request("owner", "192.168.1.2", "read")
    assert view["status"] == "approved"
    got = d.poll(view["id"])
    assert got["status"] == "approved"
    assert got["client"]["scope"] == "admin", "the first client is the owner"
    assert d.store.verify(got["token"]).scope == "admin"
    second, _ = d.request("guest", "192.168.1.3")
    assert second["status"] == "pending"
    assert not d.status()["window"]["open"]


def test_a_request_id_never_starts_with_a_dash(tmp_path, monkeypatch):
    """1 in 64 urlsafe ids started with '-', and `aurasync clients approve <id>` read it as an
    option: the CLI could not approve that request (and test_clients_cli failed at random)."""
    import aurasync.pairing as pairing_module

    ids = iter(["-starts-with-a-dash", "no-dash-here"])
    monkeypatch.setattr(pairing_module.secrets, "token_urlsafe", lambda _n: next(ids))
    d, _, _ = desk(tmp_path, window_s=0)
    view, _ = d.request("guest", "192.168.1.9")
    assert view["id"] == "no-dash-here"


def test_the_token_is_handed_over_once(tmp_path):
    d, _, _ = desk(tmp_path)
    view, _ = d.request("owner", "ip")
    assert "token" in d.poll(view["id"])
    again = d.poll(view["id"])
    assert again == {"status": "delivered"}


def test_the_window_closes_with_time_and_can_be_off(tmp_path):
    d, clock, _ = desk(tmp_path)
    clock.t += 601
    assert d.request("late", "ip")[0]["status"] == "pending"
    off, _, _ = desk(tmp_path / "b", window_s=0)
    assert off.request("x", "ip")[0]["status"] == "pending"


def test_an_admin_approves_with_a_scope_or_denies(tmp_path):
    d, _, _ = desk(tmp_path, window_s=0)
    a, _ = d.request("tablet", "ip-a", "control")
    b, _ = d.request("visitor", "ip-b")
    pending = {r["name"]: r for r in d.status()["requests"]}
    assert pending["tablet"]["check"] == a["check"], "both screens show the same check"
    assert d.approve(a["id"], "read")["client"]["scope"] == "read"
    assert d.poll(a["id"])["client"]["scope"] == "read"
    d.deny(b["id"])
    assert d.poll(b["id"]) == {"status": "denied"}
    with pytest.raises(ContractError):
        d.approve(b["id"])


def test_a_token_nobody_collects_is_revoked_with_its_request(tmp_path):
    d, clock, _ = desk(tmp_path, window_s=0)
    view, _ = d.request("phone", "ip")
    d.approve(view["id"])
    assert len(d.store) == 1
    clock.t += pairing.REQUEST_TTL_S + 1
    assert d.poll(view["id"]) is None
    assert len(d.store) == 0


def test_the_code_approves_with_the_requested_scope(tmp_path):
    d, _, shown = desk(tmp_path, window_s=0)
    code = d.start_code()["code"]
    assert shown == [code]
    view, wrong = d.request("new phone", "ip", "admin", code)
    assert not wrong
    assert view["status"] == "approved"
    assert d.poll(view["id"])["client"]["scope"] == "admin"
    again, wrong = d.request("someone else", "ip2", "admin", code)
    assert wrong, "a code works once"
    assert again["status"] == "pending"


def test_wrong_codes_burn_it(tmp_path):
    d, _, _ = desk(tmp_path, window_s=0)
    code = d.start_code()["code"]
    other = f"{(int(code) + 1) % 10**6:06d}"
    for i in range(pairing.CODE_ATTEMPTS):
        assert d.request(f"guess {i}", f"ip{i}", "admin", other)[1]
    assert not d.status()["code"]["active"]
    view, wrong = d.request("owner", "ip-owner", "admin", code)
    assert wrong
    assert view["status"] == "pending"


def test_the_code_expires(tmp_path):
    d, clock, _ = desk(tmp_path, window_s=0)
    code = d.start_code(60)["code"]
    clock.t += 61
    assert d.request("late", "ip", "admin", code)[0]["status"] == "pending"


def test_one_pending_request_per_address_and_a_cap(tmp_path):
    d, _, _ = desk(tmp_path, window_s=0)
    first, _ = d.request("a", "same-ip")
    d.request("a again", "same-ip")
    assert d.poll(first["id"]) is None, "replaced"
    for i in range(pairing.MAX_PENDING - 1):
        d.request(f"n{i}", f"ip{i}")
    with pytest.raises(ContractError) as exc:
        d.request("one too many", "ip-x")
    assert exc.value.code == "busy"
    assert len(d.status()["requests"]) == pairing.MAX_PENDING


# -- the contract's access operations -------------------------------------------------------


def test_the_access_operations_through_the_contract(tmp_path):
    acc = Access("m" * 43, ClientStore(tmp_path / "c.json"), window_s=0)

    def run(**message):
        return acc.handle(control.parse({"v": 1, **message}))

    view, _ = acc.desk.request("phone", "ip")
    assert run(op="pair_status")["result"]["requests"][0]["id"] == view["id"]
    client = run(op="pair_approve", request=view["id"])["result"]["client"]
    assert run(op="clients")["result"]["clients"][0]["id"] == client["id"]
    assert run(op="client_rename", client=client["id"], name="kitchen")["result"]["client"]["name"] == "kitchen"
    assert run(op="client_revoke", client=client["id"])["result"] == {"revoked": client["id"]}
    assert run(op="client_revoke", client=client["id"])["error"]["code"] == "not_found"
    assert len(run(op="pair_start")["result"]["code"]) == 6
    assert run(op="pair_deny", request="x" * 24)["error"]["code"] == "not_found"


def test_a_new_code_comes_with_its_connection_code(tmp_path):
    """The PWA's single code (connection_code.py): address, pairing code and the root's fingerprint."""
    from aurasync import connection_code

    fp = "F7:0C:8B:6B:D8:1B:17:BE:C9:42:A8:38:C8:4F:97:76:95:6E:BE:60:AD:95:BD:F1:CB:A0:1D:40:E3:2B:8E:5B"
    shown = []
    acc = Access("m" * 43, ClientStore(tmp_path / "c.json"), window_s=0, show_code=lambda *a: shown.append(a))

    def run(**message):
        return acc.handle(control.parse({"v": 1, **message}))["result"]

    assert run(op="pair_start")["connection_code"] is None  # no HTTPS: the PWA could not reach it
    acc.tls = {"port": 8443, "root_sha256": fp}
    acc.where = lambda: "192.168.100.11"
    started = run(op="pair_start")
    decoded = connection_code.decode(started["connection_code"])
    assert decoded == connection_code.Decoded("192.168.100.11", 8443, started["code"], "F70C8")
    assert run(op="pair_status")["code"]["connection_code"] == started["connection_code"]
    assert shown[-1] == (started["code"], 120.0, started["connection_code"])
    acc.where = lambda: "aurasync.local"  # not an IPv4: only the 6 digits
    assert run(op="pair_start")["connection_code"] is None


def test_adding_a_virtual_speaker_needs_admin():
    assert required_scope("speaker_add_virtual") == "admin"


def test_join_and_leave_need_control():
    assert required_scope("speaker_join") == "control"
    assert required_scope("speaker_leave") == "control"
