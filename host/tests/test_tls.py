"""HTTPS with the service's own root (`tls.py`, d-7c8794-37f9bc)."""

import datetime as dt
import http.client
import ipaddress
import json
import os
import plistlib
import socket
import ssl
import stat
import threading

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from aurasync import tls
from aurasync.access import Access
from aurasync.lan import LocalNames
from aurasync.rest import make_server
from aurasync.tls import CertificateKeeper, Certificates

from .test_clients_rest import TOKEN, svc_with_fake  # noqa: F401 - the fixture


def names(ips=("192.168.1.50",), host="pc-test"):
    box = {"ips": list(ips)}
    return LocalNames("0.0.0.0", addresses=lambda: box["ips"], hostname=lambda: host), box


def sans(pem_path):
    cert = x509.load_pem_x509_certificate(pem_path.read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    return set(san.get_values_for_type(x509.DNSName)) | {str(i) for i in san.get_values_for_type(x509.IPAddress)}


@pytest.fixture
def certs(tmp_path):
    c = Certificates(tmp_path / "tls")
    n, _ = names()
    c.ensure_server(n.dns_names(), n.ip_addresses(), hostname=n.hostname)
    return c


def test_files_and_permissions(certs):
    assert stat.S_IMODE(os.stat(certs.dir).st_mode) == 0o700
    for key in (certs.root_key, certs.server_key):
        assert stat.S_IMODE(key.stat().st_mode) == 0o600
    assert sans(certs.server_pem) == {
        "aurasync.local",
        "localhost",
        "pc-test",
        "pc-test.local",
        "127.0.0.1",
        "192.168.1.50",
    }


def test_the_server_certificate_is_under_apples_limit_and_the_root_is_a_constrained_ca(certs):
    cert = x509.load_pem_x509_certificate(certs.server_pem.read_bytes())
    days = (cert.not_valid_after_utc - cert.not_valid_before_utc).days
    assert days <= 398
    root = x509.load_pem_x509_certificate(certs.root_pem.read_bytes())
    assert root.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
    assert root.extensions.get_extension_for_class(x509.NameConstraints).critical


def test_names_the_root_may_not_certify_never_reach_the_certificate(tmp_path):
    c = Certificates(tmp_path / "tls")
    root = c.ensure_root("pc-test")
    dns, ips = c.wanted(["aurasync.local", "bank.example", "pc-test"], ["192.168.1.5", "8.8.8.8", "127.0.0.1"], root)
    assert dns == ["aurasync.local", "pc-test"]
    assert ips == ["127.0.0.1", "192.168.1.5"]


def test_the_root_is_made_once_and_the_server_certificate_follows_the_address(tmp_path):
    c = Certificates(tmp_path / "tls")
    n, box = names()
    assert c.ensure_server(n.dns_names(), n.ip_addresses(), hostname=n.hostname)
    root = c.root_pem.read_bytes()
    assert not c.ensure_server(n.dns_names(), n.ip_addresses(), hostname=n.hostname), "nothing changed"
    box["ips"] = ["192.168.1.77"]
    n.refresh()
    assert c.ensure_server(n.dns_names(), n.ip_addresses(), hostname=n.hostname)
    assert "192.168.1.77" in sans(c.server_pem)
    assert "192.168.1.50" not in sans(c.server_pem)
    assert c.root_pem.read_bytes() == root


def test_it_is_renewed_before_it_expires_and_the_root_stays(tmp_path):
    now = [dt.datetime.now(dt.UTC)]
    c = Certificates(tmp_path / "tls", clock=lambda: now[0])
    n, _ = names()
    c.ensure_server(n.dns_names(), n.ip_addresses())
    root = tls.fingerprint(c.root_pem.read_bytes())
    first = tls.fingerprint(c.server_pem.read_bytes())
    now[0] += dt.timedelta(days=tls.SERVER_DAYS - tls.RENEW_DAYS - 2)
    assert not c.ensure_server(n.dns_names(), n.ip_addresses())
    now[0] += dt.timedelta(days=3)
    assert c.ensure_server(n.dns_names(), n.ip_addresses())
    assert tls.fingerprint(c.server_pem.read_bytes()) != first
    assert tls.fingerprint(c.root_pem.read_bytes()) == root


def test_half_a_root_refuses(tmp_path):
    c = Certificates(tmp_path / "tls")
    c.ensure_root()
    c.root_key.unlink()
    with pytest.raises(RuntimeError, match="half of the root"):
        c.ensure_root()


def test_the_mobileconfig_carries_the_root(certs):
    profile = plistlib.loads(certs.mobileconfig("pc-test"))
    payload = profile["PayloadContent"][0]
    assert payload["PayloadType"] == "com.apple.security.root"
    assert payload["PayloadContent"] == certs.root_der()
    assert profile["PayloadUUID"] == plistlib.loads(certs.mobileconfig("pc-test"))["PayloadUUID"], "stable"


# -- a real HTTPS server -----------------------------------------------------------------


@pytest.fixture
def https(svc_with_fake, certs):  # noqa: F811
    n, box = names(ips=["127.0.0.1"])
    context = certs.server_context()
    access = Access(TOKEN)
    httpd = make_server(
        svc_with_fake, "127.0.0.1", 0, TOKEN, access=access, names=n, ssl_context=context, certificates=certs
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], certs, context, n, box
    httpd.shutdown()
    httpd.server_close()


def get(port, path, context, host="127.0.0.1", headers=None):
    conn = http.client.HTTPSConnection(host, port, context=context, timeout=10)
    conn.request("GET", path, headers={"Host": f"127.0.0.1:{port}", **(headers or {})})
    response = conn.getresponse()
    body = response.read()
    peer = conn.sock.getpeercert()
    conn.close()
    return response, body, peer


def test_a_client_that_trusts_the_root_connects(https):
    port, certs, *_ = https
    trusting = ssl.create_default_context(cafile=str(certs.root_pem))
    response, body, _ = get(port, "/v1/hello", trusting)
    assert response.status == 200
    assert json.loads(body)["result"]["service"] == "aurasync"
    response, body, _ = get(port, "/v1/state", trusting, headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status == 200


def test_a_client_without_the_root_is_refused(https):
    port, *_ = https
    with pytest.raises(ssl.SSLCertVerificationError):
        get(port, "/v1/hello", ssl.create_default_context())


def test_the_root_is_downloadable_without_a_token(https):
    port, certs, *_ = https
    trusting = ssl.create_default_context(cafile=str(certs.root_pem))
    response, body, _ = get(port, "/v1/tls/root.pem", trusting)
    assert response.status == 200
    assert body == certs.root_pem.read_bytes()
    response, body, _ = get(port, "/v1/tls/root.crt", trusting)
    assert body == certs.root_der()
    response, body, _ = get(port, "/v1/tls/root.mobileconfig", trusting)
    assert response.getheader("Content-Type") == "application/x-apple-aspen-config"
    assert b"BEGIN" not in certs.root_der()


def test_a_new_address_reaches_the_live_server(https):
    port, certs, context, n, box = https
    keeper = CertificateKeeper(certs, n, context, lambda _: None)
    box["ips"] = ["192.168.7.7"]
    assert keeper.check()
    trusting = ssl.create_default_context(cafile=str(certs.root_pem))
    _, _, peer = get(port, "/v1/hello", trusting)
    assert ("IP Address", "192.168.7.7") in peer["subjectAltName"]
    assert not keeper.check(), "the same addresses: no new certificate"


def _leaf_for(certs, name):
    """A certificate for `name` signed with the root's key, past the SAN filter."""
    root = x509.load_pem_x509_certificate(certs.root_pem.read_bytes())
    root_key = serialization.load_pem_private_key(certs.root_key.read_bytes(), None)
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(root.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
        .sign(root_key, hashes.SHA256())
    )
    return cert, key


@pytest.mark.parametrize(("name", "trusted"), [("bank.example", False), ("other.local", True)])
def test_the_root_cannot_vouch_for_a_name_outside_the_local_network(tmp_path, certs, name, trusted):
    cert, key = _leaf_for(certs, name)
    (tmp_path / "leaf.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "leaf.key").write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(tmp_path / "leaf.pem", tmp_path / "leaf.key")
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]

    def serve_once():
        conn, _ = listener.accept()
        try:
            with server_ctx.wrap_socket(conn, server_side=True) as s:
                s.recv(1)
        except (ssl.SSLError, OSError):
            pass

    threading.Thread(target=serve_once, daemon=True).start()
    client = ssl.create_default_context(cafile=str(certs.root_pem))
    raw = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        if trusted:
            client.wrap_socket(raw, server_hostname=name).close()
        else:
            with pytest.raises(ssl.SSLCertVerificationError):
                client.wrap_socket(raw, server_hostname=name)
    finally:
        raw.close()
        listener.close()


def test_the_permitted_networks_are_private():
    for net in tls.PERMITTED_NETWORKS:
        network = ipaddress.ip_network(net)
        assert network.is_private or network.is_loopback or network.is_link_local or net == "100.64.0.0/10"
