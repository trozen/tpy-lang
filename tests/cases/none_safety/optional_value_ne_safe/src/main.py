from tpy import Int32


def ne_check(x: Int32 | None, y: Int32) -> bool:
    return x != y  # tpyc: ok


print(ne_check(5, 5))
print(ne_check(3, 5))
print(ne_check(None, 5))
