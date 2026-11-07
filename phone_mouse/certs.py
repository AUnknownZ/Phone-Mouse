"""Self-signed TLS certificate for the local server.

iOS Safari only exposes motion sensors to pages served from a secure
context, so the phone page has to be loaded over HTTPS even on a LAN.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def _cert_covers(cert_path: Path, ips: list[str]) -> bool:
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    except (OSError, ValueError, x509.ExtensionNotFound):
        return False
    covered = {str(ip) for ip in san.get_values_for_type(x509.IPAddress)}
    not_after = cert.not_valid_after_utc
    fresh = not_after - dt.timedelta(days=7) > dt.datetime.now(dt.timezone.utc)
    return fresh and all(ip in covered for ip in ips)


def ensure_certificate(directory: Path, ips: list[str]) -> tuple[Path, Path]:
    """Return (cert, key) paths, creating or refreshing them if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    cert_path = directory / "cert.pem"
    key_path = directory / "key.pem"
    ips = sorted(set(ips) | {"127.0.0.1"})

    if key_path.exists() and _cert_covers(cert_path, ips):
        return cert_path, key_path

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Phone Mouse")])
    now = dt.datetime.now(dt.timezone.utc)
    alt_names: list[x509.GeneralName] = [x509.DNSName("localhost")]
    alt_names += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=365))
        .add_extension(x509.SubjectAlternativeName(alt_names), critical=False)
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    key_path.chmod(0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path
