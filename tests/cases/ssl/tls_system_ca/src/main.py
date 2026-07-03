# System trust store: create_default_context() additionally trusts the
# platform CA bundle resolved by load_default_certs(). Seams: the fixture
# cert injected at the FRONT of ssl._ca_probe_paths, and the in-process env
# snapshot (os.environ["SSL_CERT_FILE"] = ..., the ospath_expanduser
# precedent). Arms: probe-injected trust; untrusted-rejected (additive, not
# over-trust); garbage probe entry skipped best-effort (CPython/OpenSSL
# match) with the explicit cafile still verifying; env wins over the probe
# list; garbage SSL_CERT_FILE skipped likewise. Any inherited SSL_CERT_FILE
# is deleted at start so a host's real env cannot flip the arms.
from typing import Final
import os
import ssl
from ssl import SSLSocket  # qualified ssl.SSLSocket annotations are unsupported
import socket

CERT_PEM: Final[str] = """-----BEGIN CERTIFICATE-----
MIIBlTCCATugAwIBAgIUe2CartEUhHtqoCYrRV89NXxqz8EwCgYIKoZIzj0EAwIw
FDESMBAGA1UEAwwJbG9jYWxob3N0MCAXDTI2MDYyOTE1MDY1NloYDzIxMjYwNjA1
MTUwNjU2WjAUMRIwEAYDVQQDDAlsb2NhbGhvc3QwWTATBgcqhkjOPQIBBggqhkjO
PQMBBwNCAATi4r8fZOEM8tz66TgRALGG7z33xtTCAHavwkRqu8crpAaMoNVIsMxE
tP9yXT/7crk2Jpju9JqnkjzM/iLZ5gbqo2kwZzAdBgNVHQ4EFgQUcHp1/TdGBPiN
WGIQoSCKEgty4yUwHwYDVR0jBBgwFoAUcHp1/TdGBPiNWGIQoSCKEgty4yUwDwYD
VR0TAQH/BAUwAwEB/zAUBgNVHREEDTALgglsb2NhbGhvc3QwCgYIKoZIzj0EAwID
SAAwRQIgE8EzoNEb464cVe4PlS6BpNoBLmBWGkwUQ9mTi5JqX5UCIQCRCx3f+YQW
Ddslcyu0U0qfufOT/QbqMaDSyosTTmLteQ==
-----END CERTIFICATE-----
"""

KEY_PEM: Final[str] = """-----BEGIN PRIVATE KEY-----
MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQg2kn/USvpv4Ilspd2
xfLz4BM0UjqqhFJndB7QYY+ijAihRANCAATi4r8fZOEM8tz66TgRALGG7z33xtTC
AHavwkRqu8crpAaMoNVIsMxEtP9yXT/7crk2Jpju9JqnkjzM/iLZ5gbq
-----END PRIVATE KEY-----
"""

CERT_PATH: Final[str] = "/tmp/tpy_test_ssl_sysca_cert.pem"
KEY_PATH: Final[str] = "/tmp/tpy_test_ssl_sysca_key.pem"
GARBAGE_PATH: Final[str] = "/tmp/tpy_test_ssl_sysca_garbage.pem"


def write_fixtures() -> None:
    with open(CERT_PATH, "w") as f:
        f.write(CERT_PEM)
    with open(KEY_PATH, "w") as f:
        f.write(KEY_PEM)
    with open(GARBAGE_PATH, "w") as f:
        f.write("not a certificate\n")


def drive(cli: SSLSocket, srv: SSLSocket) -> bool:
    """Alternate handshake steps until both sides complete (non-blocking)."""
    cdone = False
    sdone = False
    i = 0
    while i < 500 and not (cdone and sdone):
        if not sdone and srv.do_handshake():
            sdone = True
        if not cdone and cli.do_handshake():
            cdone = True
        i += 1
    return cdone and sdone


def try_verify(cafile: str = "") -> bool:
    """One default-context handshake against the fixture peer; True if the
    client's verification accepted the peer cert. A non-empty `cafile` is
    loaded explicitly (load_verify_locations), on top of the defaults."""
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    ctx = ssl.create_default_context()
    if len(cafile) > 0:
        ctx.load_verify_locations(cafile)
    cli = ctx.wrap_socket(a, "localhost", False)
    srv = ssl._wrap_server(b, CERT_PATH, KEY_PATH)
    sdone = False
    i = 0
    while i < 500:
        if not sdone:
            try:
                if srv.do_handshake():
                    sdone = True
            except ssl.SSLError:
                return False  # server aborts after the client rejects
        try:
            if cli.do_handshake():
                return True
        except ssl.SSLCertVerificationError:
            return False
        i += 1
    return False


def main() -> None:
    write_fixtures()
    # Hermeticity: a host's real SSL_CERT_FILE would override the probe seam
    # and flip the arms below.
    if "SSL_CERT_FILE" in os.environ:
        del os.environ["SSL_CERT_FILE"]

    # Fixture bundle injected as the "system store": default context verifies.
    ssl._ca_probe_paths.insert(0, CERT_PATH)
    print("system-trusted:", try_verify())

    # Probe list restored: the same peer is untrusted again -- the system
    # store is additive, it does not blanket-accept.
    ssl._ca_probe_paths.pop(0)
    print("untrusted rejected:", not try_verify())

    # A garbage bundle at the probed location is skipped best-effort
    # (CPython/OpenSSL match: never raises) -- the explicitly loaded cafile
    # still verifies the peer.
    ssl._ca_probe_paths.insert(0, GARBAGE_PATH)
    print("garbage probe skipped:", try_verify(CERT_PATH))
    ssl._ca_probe_paths.pop(0)

    # SSL_CERT_FILE wins over the probe list (the probe list is untouched
    # here and would not trust the peer).
    os.environ["SSL_CERT_FILE"] = CERT_PATH
    print("env wins:", try_verify())

    # A garbage SSL_CERT_FILE is skipped best-effort too; the explicit
    # cafile still verifies.
    os.environ["SSL_CERT_FILE"] = GARBAGE_PATH
    print("garbage env skipped:", try_verify(CERT_PATH))
    del os.environ["SSL_CERT_FILE"]


main()
