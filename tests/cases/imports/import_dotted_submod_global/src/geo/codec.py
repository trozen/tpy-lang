from tpy import Int32

_HEX: bytes = b"0123456789ABCDEF"


def hi_nibble(c: Int32) -> Int32:
    return Int32(_HEX[c >> 4])
