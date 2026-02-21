from tpy import Int32


def add_one(x: Int32 | None) -> Int32:
    assert x is not None
    return x + 1


print(add_one(41))
