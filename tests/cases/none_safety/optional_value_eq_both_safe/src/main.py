from tpy import Int32


def eq_both(a: Int32 | None, b: Int32 | None) -> bool:
    return a == b  # tpyc: ok


print(eq_both(5, 5))
print(eq_both(5, 3))
print(eq_both(None, 5))
print(eq_both(5, None))
print(eq_both(None, None))
