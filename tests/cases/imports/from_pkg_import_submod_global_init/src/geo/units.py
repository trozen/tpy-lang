from tpy import int32

_SCALE: int32 = 100


def scaled(x: int32) -> int32:
    return x * _SCALE
