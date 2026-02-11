from tpy import Int32


def bump_positive(n: Int32) -> Int32:
    assert n > 0, "n must be positive"
    return n + 1


print(bump_positive(4))
