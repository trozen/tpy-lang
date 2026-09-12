from tpy import int32

_B: bytes = b"BBBB"


def b_first() -> int32:
    return int32(_B[0])
