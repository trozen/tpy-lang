from tpy import Int32

_B: bytes = b"BBBB"


def b_first() -> Int32:
    return Int32(_B[0])
