from tpy import int32


def negate(x: int32 | None) -> int32:
    return -x


print(negate(3))
print(negate(None))
