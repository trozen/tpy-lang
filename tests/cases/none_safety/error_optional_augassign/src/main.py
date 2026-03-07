from tpy import Int32


def bump(x: Int32 | None) -> Int32:
    x += 1  # tpyc: error(/not supported for/)
    return 0
