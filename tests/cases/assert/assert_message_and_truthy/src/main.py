from tpy import int32


def bump_positive(n: int32) -> int32:
    assert n > 0, "n must be positive"
    return n + 1


print(bump_positive(4))
