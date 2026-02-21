from tpy import Int32


def shrink(x: Int32 | None) -> Int32:
    while x is not None and x > 0:
        x = x - 1  # tpyc: ok
    return 0


print(shrink(2))
print(shrink(0))
print(shrink(None))
