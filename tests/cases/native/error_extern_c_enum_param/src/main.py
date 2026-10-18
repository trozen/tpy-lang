# A TPy enum at a C-linkage parameter is refused (a C caller could pass a non-member);
# the hint names the checked crossing through the enum's underlying width.
from enum import IntEnum
from tpy import int32
from tpy.extern import export


class Mode(IntEnum):
    OFF = 0
    ON = 1


@export(binding="C")
def set_mode(m: Mode) -> int32:  # tpyc: error(/parameter 'm': type 'Mode' is not representable in the C ABI; pass its value as int32: send '\.value', receive by calling the enum on the integer/)
    return m.value
