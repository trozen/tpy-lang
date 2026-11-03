# Section native_enum_dep: a cycle member's @native enum whose C++ declaration
# comes from an imported native module's includes, not its own.
from enum import Enum
from tpy import int32
from tpy.extern import native
import tint_nat
import tint_peer


@native("tints::Col")
class Col(Enum):
    RED = 1
    BLUE = 2


def pick() -> int32:
    return tint_nat.base() + tint_peer.offset()
