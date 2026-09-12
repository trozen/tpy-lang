from tpy import int32


def ne_check(x: int32 | None, y: int32) -> bool:
    return x != y  # tpyc: ok


print(ne_check(5, 5))
print(ne_check(3, 5))
print(ne_check(None, 5))
