from tpy import Int32


def is_large(x: Int32 | None) -> Int32:
    assert x is not None
    if x > 10:  # tpyc: ok
        return 1
    return 0


print(is_large(20))
print(is_large(5))
