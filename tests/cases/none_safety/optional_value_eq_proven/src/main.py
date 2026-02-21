from tpy import Int32


def eq_proven(x: Int32 | None, y: Int32) -> bool:
    assert x is not None
    return x == y  # tpyc: ok


print(eq_proven(5, 5))
print(eq_proven(3, 5))
