from tpy import int32


def negate_checked(x: int32 | None) -> int32:
    assert x is not None
    return -x  # tpyc: ok


print(negate_checked(3))
