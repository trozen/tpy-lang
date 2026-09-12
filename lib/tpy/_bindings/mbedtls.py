# tpy: cpp_namespace("tpystd::_bindings::mbedtls")
# tpy: include("<tpy/stdlib/mbedtls_h.hpp>")
# tpy: link("mbedtls", managed=True)
"""Raw mbedTLS C bindings. Not for direct user import.

The handshake surface is the cohesive `tpy_tls_*` session API implemented
in runtime/cpp/src/stdlib/mbedtls_shim.c (see that file + the facade header
for why the FFI is shaped as one opaque session rather than per-handle
mirrors). The public `ssl` module builds SSLContext / SSLSocket on top of a
single `Ptr[Session]`, using __del__ for RAII over the session lifetime.

String args follow the socket.py convention: a (view pointer, length) pair
that is NOT NUL-terminated; the shim copies + terminates internally.
"""

from tpy import Ptr, uint8, uint32, int32, uint64, readonly
from tpy.extern import native


# Opaque session handle: `Ptr[Session]` codegens to `tpy_tls_session*`.
@native("::tpy_tls_session")
class Session: ...


@native("::mbedtls_version_get_number")
def version_get_number() -> uint32: ...


# ---------- lifetime ----------
@native("::tpy_tls_new")
def tls_new() -> Ptr[Session]: ...

@native("::tpy_tls_free")
def tls_free(s: Ptr[Session]) -> None: ...


# ---------- configuration ----------
@native("::tpy_tls_config_client")
def tls_config_client(s: Ptr[Session], ca_path: Ptr[readonly[uint8]],
                      ca_path_len: uint64, verify: int32) -> int32: ...

@native("::tpy_tls_config_server")
def tls_config_server(s: Ptr[Session],
                      cert_path: Ptr[readonly[uint8]], cert_path_len: uint64,
                      key_path: Ptr[readonly[uint8]], key_path_len: uint64) -> int32: ...

@native("::tpy_tls_add_bundled_ca")
def tls_add_bundled_ca(s: Ptr[Session]) -> int32: ...

@native("::tpy_tls_add_ca_file")
def tls_add_ca_file(s: Ptr[Session], path: Ptr[readonly[uint8]],
                    path_len: uint64) -> int32: ...

@native("::tpy_tls_bundled_ca_count")
def tls_bundled_ca_count() -> int32: ...

@native("::tpy_tls_setup")
def tls_setup(s: Ptr[Session]) -> int32: ...

@native("::tpy_tls_set_fd")
def tls_set_fd(s: Ptr[Session], fd: int32) -> None: ...

@native("::tpy_tls_set_hostname")
def tls_set_hostname(s: Ptr[Session], host: Ptr[readonly[uint8]],
                     host_len: uint64) -> int32: ...


# ---------- handshake + application data ----------
@native("::tpy_tls_handshake")
def tls_handshake(s: Ptr[Session]) -> int32: ...

@native("::tpy_tls_read")
def tls_read(s: Ptr[Session], buf: Ptr[uint8], length: uint64) -> int32: ...

@native("::tpy_tls_write")
def tls_write(s: Ptr[Session], buf: Ptr[readonly[uint8]], length: uint64) -> int32: ...

@native("::tpy_tls_close_notify")
def tls_close_notify(s: Ptr[Session]) -> int32: ...


# ---------- introspection + errors ----------
@native("::tpy_tls_verify_result")
def tls_verify_result(s: Ptr[Session]) -> uint32: ...

@native("::tpy_tls_version")
def tls_version(s: Ptr[Session]) -> Ptr[readonly[uint8]]: ...

@native("::tpy_tls_classify")
def tls_classify(rc: int32) -> int32: ...

@native("::tpy_tls_strerror")
def tls_strerror(code: int32, buf: Ptr[uint8], length: uint64) -> None: ...
