# tpy: cpp_namespace("tpystd::_bindings::mbedtls")
# tpy: include("<tpy/stdlib/mbedtls_h.hpp>")
# tpy: link("mbedtls", managed=True)
"""Raw mbedTLS C bindings. Not for direct user import.

One @native declaration per mbedtls_* primitive, matching the upstream C
ABI 1:1. No Python semantics. The public `ssl` facade builds user-visible
SSLContext / SSLSocket classes on top of these raw handles, using __del__
for RAII over the opaque mbedtls_ssl_context* / mbedtls_ssl_config*
lifetimes.

v1: only the version probe is bound; the handshake / x509 / pk / entropy /
ctr_drbg / net surface lands with the ssl module.
"""

from tpy import UInt32
from tpy.extern import native


@native("::mbedtls_version_get_number")
def version_get_number() -> UInt32: ...
