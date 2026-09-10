# tpy: ext_module
# CPython extension over the bytes boundary: bytes params/returns marshal by
# copy. bytes is immutable, so the boundary copy is unobservable; a non-bytes
# argument (including bytearray, a mutable buffer that is not marshallable by
# value) is a TypeError enforced only by the compiled .so and lives in
# ext_checks.py.
from tpy import Own
from tpy.extern import export


@export
def echo(data: bytes) -> bytes:
    return data


# Own[bytes] admits (Own unwraps to the value type bytes); contrast Own[bytearray],
# which is rejected -- see tests/cases/interop/error_export_bytearray_return.
@export
def make_own() -> Own[bytes]:
    return b"owned"


@export
def cat(a: bytes, b: bytes) -> bytes:
    return a + b


@export
def shout(data: bytes) -> bytes:
    return data.upper()
