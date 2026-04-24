# Aug-assign on a literal-initialized local replaces its storage with a
# freshly-allocated owned value. Any prior param-derived provenance seeded
# by the literal initializer must be cleared, otherwise `return b as
# BytesView` would pass the dangling check despite pointing into the local
# owned bytes.
from tpy import BytesView

def bad_bytes_augmented() -> BytesView:
    b: bytes = b"hi"
    b += b"x"
    return b  # tpyc: error(/Cannot return BytesView referencing a local or temporary/)
