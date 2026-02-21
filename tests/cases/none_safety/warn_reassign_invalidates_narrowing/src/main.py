from tpy import Int32


def bad(x: Int32 | None) -> Int32:
    assert x is not None
    x = None
    return x + 1  # tpyc: warning(/Potential None access/)
