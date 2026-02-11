from tpy import Int32


def use(x: Int32 | None) -> Int32:
    assert x
    return x + 1  # tpyc: warning(/Potential None access/)
