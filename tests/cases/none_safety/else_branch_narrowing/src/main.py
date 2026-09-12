from tpy import int32


def describe(x: int32 | None) -> int32:
    if x is None:
        return -1
    else:
        return x + 1


print(describe(10))
print(describe(None))
