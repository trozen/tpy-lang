from tpy import int32


def bump(x: int32 | None) -> int32:
    x += 1  # tpyc: error(/not supported for/)
    return 0
