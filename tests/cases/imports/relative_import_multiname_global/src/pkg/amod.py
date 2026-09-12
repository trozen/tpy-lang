from tpy import int32

_A: bytes = b"AAAA"


def a_first() -> int32:
    return int32(_A[0])
