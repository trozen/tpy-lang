from tpy import Int32


def add_if_both(a: Int32 | None, b: Int32 | None) -> Int32:
    if a and b:  # tpyc: warning(/variable 'a'/)  # tpyc: warning(/variable 'b'/)
        return a + b  # tpyc: ok
    return 0


print(add_if_both(1, 2))
print(add_if_both(0, 2))
print(add_if_both(None, 2))
