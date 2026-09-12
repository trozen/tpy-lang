from tpy import int32


def is_large(x: int32 | None) -> int32:
    assert x is not None
    if x > 10:  # tpyc: ok
        return 1
    return 0


print(is_large(20))
print(is_large(5))
