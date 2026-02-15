from tpy import Int32


def eq_check(x: Int32 | None, y: Int32) -> bool:
    return x == y  # tpyc: ok


print(eq_check(5, 5))
print(eq_check(3, 5))
print(eq_check(None, 5))
