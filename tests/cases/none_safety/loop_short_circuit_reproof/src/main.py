from tpy import int32


def shrink(x: int32 | None) -> int32:
    while x is not None and x > 0:
        x = x - 1  # tpyc: ok
    return 0


print(shrink(2))
print(shrink(0))
print(shrink(None))
