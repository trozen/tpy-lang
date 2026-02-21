from tpy import Int32


def use(x: Int32 | None) -> Int32:
    assert x  # tpyc: warning(/Truthiness check on optional value/)
    return x + 1  # tpyc: ok


print(use(41))
