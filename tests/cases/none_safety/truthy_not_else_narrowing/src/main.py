from tpy import int32


def use_else(x: int32 | None) -> int32:
    if not x:  # tpyc: warning(/Truthiness check on optional value/)
        return 0
    else:
        return x + 1  # tpyc: ok


print(use_else(2))
print(use_else(0))
print(use_else(None))
