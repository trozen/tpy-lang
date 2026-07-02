from tpy import Int32

_SCALE: Int32 = 100


def scaled(x: Int32) -> Int32:
    return x * _SCALE
