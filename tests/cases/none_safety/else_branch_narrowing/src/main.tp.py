from tpy import Int32


def describe(x: Int32 | None) -> Int32:
    if x is None:
        return -1
    else:
        return x + 1


print(describe(10))
print(describe(None))
