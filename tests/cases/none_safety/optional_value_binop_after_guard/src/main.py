from tpy import int32


def add_after_guard(x: int32 | None) -> int32:
    if x is not None:
        return x + 1  # tpyc: ok
    return 0


print(add_after_guard(2))
print(add_after_guard(None))
