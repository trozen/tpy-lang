from tpy import int32


def eq_check(x: int32 | None, y: int32) -> bool:
    return x == y  # tpyc: ok


print(eq_check(5, 5))
print(eq_check(3, 5))
print(eq_check(None, 5))
