from tpy import int32


def clamp_positive(x: int32 | None) -> int32:
    assert x is not None and x > 0, "need positive"
    return x + 1


print(clamp_positive(5))
