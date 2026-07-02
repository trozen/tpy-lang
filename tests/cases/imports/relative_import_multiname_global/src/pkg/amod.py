from tpy import Int32

_A: bytes = b"AAAA"


def a_first() -> Int32:
    return Int32(_A[0])
