from tpy import Int32


def negate(x: Int32 | None) -> Int32:
    return -x


print(negate(3))
print(negate(None))
