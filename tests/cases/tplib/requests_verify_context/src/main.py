# requests' verify= maps to the TLS context: True -> verified default
# (CERT_REQUIRED + hostname check), False -> CERT_NONE + no hostname check,
# "<path>" -> a CA file loaded while verification stays required.
# no_cpython: drives the ssl module's SSLContext (real mbedTLS), no CPython ssl.
from typing import Final
from tpy import String
import ssl
from tplib.requests import _ssl_context_for


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

CA_PATH: Final[str] = "/tmp/tpy_test_verify_ca.pem"


def main() -> None:
    secure = _ssl_context_for(True)
    print(secure.verify_mode == ssl.CERT_REQUIRED, secure.check_hostname)

    insecure = _ssl_context_for(False)
    print(insecure.verify_mode == ssl.CERT_NONE, insecure.check_hostname)

    with open(CA_PATH, "w") as f:
        f.write(CERT_PEM)
    # String(): a bare str view arg doesn't auto-coerce into the bool|str
    # variant param yet (it does once it's a union-typed local, as in curl.py).
    custom = _ssl_context_for(String(CA_PATH))
    # A custom CA file adds trust but keeps verification on.
    print(custom.verify_mode == ssl.CERT_REQUIRED, custom.check_hostname)


main()
