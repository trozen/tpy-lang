from tpy import int32


def add_if_both(a: int32 | None, b: int32 | None) -> int32:
    if a and b:  # tpyc: warning(/variable 'a'/)  # tpyc: warning(/variable 'b'/)
        return a + b  # tpyc: ok
    return 0


print(add_if_both(1, 2))
print(add_if_both(0, 2))
print(add_if_both(None, 2))
