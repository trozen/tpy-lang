from tpy import Int32


def clamp_positive(x: Int32 | None) -> Int32:
    assert x is not None and x > 0, "need positive"
    return x + 1


print(clamp_positive(5))
