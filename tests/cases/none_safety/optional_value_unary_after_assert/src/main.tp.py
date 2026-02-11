from tpy import Int32


def negate_checked(x: Int32 | None) -> Int32:
    assert x is not None
    return -x  # tpyc: ok


print(negate_checked(3))
