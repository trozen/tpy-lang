from tpy import int32

_HEX: bytes = b"0123456789ABCDEF"


def hi_nibble(c: int32) -> int32:
    return int32(_HEX[c >> 4])
