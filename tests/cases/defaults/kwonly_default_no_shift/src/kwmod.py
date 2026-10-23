from tpy import int64


def scaled(a: int64, *, k: int64 = 5) -> int64:
    return a * 10 + k
