__tpy__ = {"mod": "native", "cpp_namespace": "pair", "includes": ["cycle_types.hpp"]}
from tpy import int32
from tpy.extern import native, native_field
from nat_a import Inner

@native
class Outer:
    # The peer's native type held by value: native modules are no C++ peers.
    held: Inner = native_field("held")
    tag: int32

    def __init__(self) -> None: ...
