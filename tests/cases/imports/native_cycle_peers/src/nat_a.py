# Section native_pair: a native module in an import cycle with another one.
__tpy__ = {"mod": "native", "cpp_namespace": "pair", "includes": ["cycle_types.hpp"]}
from typing import Final
from tpy import int32
from tpy.extern import native, native_global

@native
class Inner:
    v: int32

# A native global binding is a declaration, allowed in a cycle member.
LIMIT: Final[int32] = native_global("pair::LIMIT")

# Late: nat_b imports Inner back from this module, which must exist first.
from nat_b import Outer  # tpyc: warning(/Import statement should be at the top/)

@native
def peek(o: Outer) -> int32: ...
