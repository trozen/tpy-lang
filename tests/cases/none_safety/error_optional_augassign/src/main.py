from tpy import Int32


def bump(x: Int32 | None) -> Int32:
    x += 1  # tpyc: error(/Augmented assignment target must be a numeric type/)
    return 0
