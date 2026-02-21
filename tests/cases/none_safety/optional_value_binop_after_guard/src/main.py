from tpy import Int32


def add_after_guard(x: Int32 | None) -> Int32:
    if x is not None:
        return x + 1  # tpyc: ok
    return 0


print(add_after_guard(2))
print(add_after_guard(None))
