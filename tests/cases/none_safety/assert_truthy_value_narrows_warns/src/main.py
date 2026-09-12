from tpy import int32


def use(x: int32 | None) -> int32:
    assert x  # tpyc: warning(/Truthiness check on optional value/)
    return x + 1  # tpyc: ok


print(use(41))
