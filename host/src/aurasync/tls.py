"""HTTPS with the service's own certificate authority (d-7c8794-37f9bc).

A PWA served from GitHub Pages is an HTTPS page; on an iPhone it cannot talk to
`http://192.168…` at all (mixed content, research H §1.4), so the device serves its API over
HTTPS too. No public CA can certify `aurasync.local` or a private address, so the service makes
its own:

- a **root**, self-signed, made once and kept for `ROOT_DAYS` (installing it on a phone is
  what removes the warning, so it must not change);
- a **server certificate** signed by it for `aurasync.local`, the host name, `localhost` and the
  machine's current addresses, valid for `SERVER_DAYS` (under Apple's 398-day limit). It is
  made again when an address changes or `RENEW_DAYS` before it expires; the root stays.

**The root can only vouch for local names.** Its `NameConstraints` (critical) permit
`.local`, `localhost`, the host name it was made with, and private, loopback, link-local and
CGNAT addresses. A phone that trusts it does not let it certify `bank.example`: the risk mkcert
warns about (a root key that "gives complete power to intercept secure requests", research H
§2) shrinks to the local network. A name outside them never goes into the server certificate,
since one excluded SAN invalidates the whole certificate.

Files in `<config>/tls/` (directory 0700, keys 0600): `root.key`, `root.pem`, `server.key`,
`server.pem`. Revert: stop the service, delete the directory, remove the root from each phone.

`cryptography` and not the `openssl` program: `cryptography` is already installed with the
package (Bumble requires it, with wheels for Linux aarch64 and macOS arm64), it builds name
constraints and SANs as objects instead of a config file, and the `openssl` of macOS is
LibreSSL, whose `req`/`x509` flags differ from OpenSSL's.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import ipaddress
import os
import plistlib
import ssl
import threading
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from aurasync.presets import write_atomic

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from aurasync.lan import LocalNames

ROOT_DAYS = 3650
SERVER_DAYS = 397
"""Apple refuses TLS server certificates valid for more than 398 days (support.apple.com
HT211025). The rule is written for public CAs; staying under it costs nothing."""
RENEW_DAYS = 30
CHECK_S = 60.0
"""How often `CertificateKeeper` looks at the addresses and the expiry."""

PERMITTED_NETWORKS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "fc00::/7",
    "fe80::/10",
    "::1/128",
)
"""Private (RFC 1918, ULA), loopback, link-local, and CGNAT (Tailscale and similar)."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def fingerprint(pem: bytes) -> str:
    """SHA-256 of the certificate's DER, as uppercase hex pairs (what browsers show)."""
    digest = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode())).hexdigest().upper()
    return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))


def _write_key(path: Path, key: ec.EllipticCurvePrivateKey) -> None:
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    write_atomic(path, pem.decode(), mode=0o600)


def _read_key(path: Path) -> ec.EllipticCurvePrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        msg = f"{path} is not an EC key"
        raise TypeError(msg)
    return key


def _permitted(name: str | ipaddress.IPv4Address | ipaddress.IPv6Address, root: x509.Certificate) -> bool:
    """Whether the root's name constraints let it certify `name`."""
    try:
        nc = root.extensions.get_extension_for_class(x509.NameConstraints).value
    except x509.ExtensionNotFound:
        return True
    subtrees = nc.permitted_subtrees or []
    if isinstance(name, str):
        name = name.lower()
        for tree in subtrees:
            if isinstance(tree, x509.DNSName):
                base = tree.value.lower().lstrip(".")
                if name == base or name.endswith("." + base):
                    return True
        return False
    return any(isinstance(tree, x509.IPAddress) and name in tree.value for tree in subtrees)


@dataclass(frozen=True)
class TlsInfo:
    directory: Path
    root_pem: Path
    root_sha256: str
    cert_pem: Path
    cert_sha256: str
    names: tuple[str, ...]
    not_after: str

    def as_dict(self) -> dict:
        return {
            "root_sha256": self.root_sha256,
            "cert_sha256": self.cert_sha256,
            "names": list(self.names),
            "not_after": self.not_after,
        }


class Certificates:
    """The root and the server certificate in one directory."""

    def __init__(self, directory: Path, clock: Callable[[], dt.datetime] = _now) -> None:
        self.dir = directory
        self.clock = clock
        self.root_key = directory / "root.key"
        self.root_pem = directory / "root.pem"
        self.server_key = directory / "server.key"
        self.server_pem = directory / "server.pem"
        self._lock = threading.Lock()

    # -- the root ---------------------------------------------------------------------

    def ensure_root(self, hostname: str = "") -> x509.Certificate:
        """Load the root, or make it the first time. Never replaced once it exists."""
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        if self.root_pem.exists() and self.root_key.exists():
            return x509.load_pem_x509_certificate(self.root_pem.read_bytes())
        if self.root_pem.exists() != self.root_key.exists():
            msg = f"{self.dir} has only half of the root (root.pem or root.key). Delete both to make a new one"
            raise RuntimeError(msg)
        key = ec.generate_private_key(ec.SECP256R1())
        host = hostname.lower().strip(".")
        label = f"aurasync local root ({host})" if host else "aurasync local root"
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
        dns = ["local", "localhost"]
        if host and not host.endswith(".local") and _is_dns(host):
            dns.append(host)
        permitted: list[x509.GeneralName] = [x509.DNSName(d) for d in dns]
        permitted += [x509.IPAddress(ipaddress.ip_network(n)) for n in PERMITTED_NETWORKS]
        now = self.clock()
        cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=ROOT_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.NameConstraints(permitted_subtrees=permitted, excluded_subtrees=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        _write_key(self.root_key, key)
        write_atomic(self.root_pem, cert.public_bytes(serialization.Encoding.PEM).decode(), mode=0o644)
        return cert

    # -- the server certificate -------------------------------------------------------

    def _current(self) -> x509.Certificate | None:
        if not (self.server_pem.exists() and self.server_key.exists()):
            return None
        try:
            return x509.load_pem_x509_certificate(self.server_pem.read_bytes())
        except ValueError:
            return None

    @staticmethod
    def _sans(cert: x509.Certificate) -> set[str]:
        try:
            san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        except x509.ExtensionNotFound:
            return set()
        return {n.lower() for n in san.get_values_for_type(x509.DNSName)} | {
            str(ip) for ip in san.get_values_for_type(x509.IPAddress)
        }

    def wanted(self, dns: list[str], ips: list[str], root: x509.Certificate) -> tuple[list[str], list[str]]:
        """The SANs to put in the certificate: what the root may certify, nothing else."""
        keep_dns = [d.lower() for d in dns if _is_dns(d) and _permitted(d, root)]
        keep_ips = [ip for ip in ips if _permitted(ipaddress.ip_address(ip), root)]
        return sorted(set(keep_dns)), sorted(set(keep_ips))

    def ensure_server(self, dns: list[str], ips: list[str], *, hostname: str = "") -> bool:
        """Make the server certificate if it is missing, names other things, or expires soon.
        True when a new one was written."""
        with self._lock:
            root = self.ensure_root(hostname)
            keep_dns, keep_ips = self.wanted(dns, ips, root)
            current = self._current()
            if current is not None:
                fresh = current.not_valid_after_utc - self.clock() > dt.timedelta(days=RENEW_DAYS)
                if fresh and self._sans(current) == {*keep_dns, *keep_ips} and self._signed_by(current, root):
                    return False
            self._issue(root, keep_dns, keep_ips)
            return True

    @staticmethod
    def _signed_by(cert: x509.Certificate, root: x509.Certificate) -> bool:
        try:
            cert.verify_directly_issued_by(root)
        except (ValueError, TypeError, Exception):  # noqa: BLE001 - InvalidSignature and friends
            return False
        return True

    def _issue(self, root: x509.Certificate, dns: list[str], ips: list[str]) -> None:
        root_key = _read_key(self.root_key)
        key = ec.generate_private_key(ec.SECP256R1())
        now = self.clock()
        names: list[x509.GeneralName] = [x509.DNSName(d) for d in dns]
        names += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns[0] if dns else "aurasync")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(root.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=SERVER_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(root.public_key()),  # type: ignore[arg-type]
                critical=False,
            )
            .sign(root_key, hashes.SHA256())
        )
        # The key first: a reader that sees the new certificate must find its key.
        _write_key(self.server_key, key)
        write_atomic(self.server_pem, cert.public_bytes(serialization.Encoding.PEM).decode(), mode=0o644)

    # -- what the service shows -------------------------------------------------------

    def info(self) -> TlsInfo:
        root = self.root_pem.read_bytes()
        cert_bytes = self.server_pem.read_bytes()
        cert = x509.load_pem_x509_certificate(cert_bytes)
        return TlsInfo(
            directory=self.dir,
            root_pem=self.root_pem,
            root_sha256=fingerprint(root),
            cert_pem=self.server_pem,
            cert_sha256=fingerprint(cert_bytes),
            names=tuple(sorted(self._sans(cert))),
            not_after=cert.not_valid_after_utc.isoformat(),
        )

    def root_der(self) -> bytes:
        return ssl.PEM_cert_to_DER_cert(self.root_pem.read_text())

    def mobileconfig(self, label: str = "") -> bytes:
        """An iOS configuration profile with the root (unsigned: iOS shows it as "Not Verified",
        and after installing it the root still has to be enabled in Certificate Trust Settings).

        The UUIDs derive from the root's fingerprint, so installing it twice replaces it."""
        der = self.root_der()
        digest = hashlib.sha256(der).hexdigest()
        ns = uuid.UUID("6f1c2a0e-4d5b-4c8e-9a51-1d3c5e7b9f20")
        short = digest[:12]
        title = f"aurasync ({label})" if label else "aurasync"
        payload = {
            "PayloadContent": [
                {
                    "PayloadType": "com.apple.security.root",
                    "PayloadVersion": 1,
                    "PayloadIdentifier": f"local.aurasync.root.{short}",
                    "PayloadUUID": str(uuid.uuid5(ns, "root:" + digest)).upper(),
                    "PayloadDisplayName": f"{title}: local root",
                    "PayloadCertificateFileName": "aurasync-root.cer",
                    "PayloadContent": der,
                }
            ],
            "PayloadType": "Configuration",
            "PayloadVersion": 1,
            "PayloadIdentifier": f"local.aurasync.profile.{short}",
            "PayloadUUID": str(uuid.uuid5(ns, "profile:" + digest)).upper(),
            "PayloadDisplayName": title,
            "PayloadDescription": (
                "Lets this phone trust the aurasync service on your local network. "
                "The root can only certify .local names and private addresses."
            ),
            "PayloadRemovalDisallowed": False,
        }
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML)

    def server_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.server_pem, self.server_key)
        return context

    def reload(self, context: ssl.SSLContext) -> None:
        """Put the current certificate into a live context: the next handshakes use it."""
        context.load_cert_chain(self.server_pem, self.server_key)


def _is_dns(name: str) -> bool:
    if not name or len(name) > 253:  # noqa: PLR2004
        return False
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        return False
    return all(label and len(label) <= 63 and label.replace("-", "").isalnum() for label in name.split("."))  # noqa: PLR2004


class CertificateKeeper:
    """Keeps the server certificate matching the machine's addresses while the service runs."""

    def __init__(
        self, certs: Certificates, names: LocalNames, context: ssl.SSLContext, log: Callable[[str], None]
    ) -> None:
        self.certs = certs
        self.names = names
        self.context = context
        self.log = log
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def check(self) -> bool:
        self.names.refresh()
        renewed = self.certs.ensure_server(
            self.names.dns_names(), self.names.ip_addresses(), hostname=self.names.hostname
        )
        if renewed:
            self.certs.reload(self.context)
            info = self.certs.info()
            self.log(f"tls: new server certificate for {', '.join(info.names)} (sha256 {info.cert_sha256[:23]}…)")
        return renewed

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(CHECK_S):
                try:
                    self.check()
                except Exception as exc:  # noqa: BLE001 - the old certificate keeps serving
                    self.log(f"tls: could not renew the certificate: {exc!r}")

        self._thread = threading.Thread(target=loop, name="aurasync-tls", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def pem_to_base64_der(pem: bytes) -> str:
    return base64.b64encode(ssl.PEM_cert_to_DER_cert(pem.decode())).decode()
