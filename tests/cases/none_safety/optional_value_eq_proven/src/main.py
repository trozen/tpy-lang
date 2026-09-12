from tpy import int32


def eq_proven(x: int32 | None, y: int32) -> bool:
    assert x is not None
    return x == y  # tpyc: ok


print(eq_proven(5, 5))
print(eq_proven(3, 5))
