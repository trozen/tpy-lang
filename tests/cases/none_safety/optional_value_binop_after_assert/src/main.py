from tpy import int32


def add_one(x: int32 | None) -> int32:
    assert x is not None
    return x + 1


print(add_one(41))
