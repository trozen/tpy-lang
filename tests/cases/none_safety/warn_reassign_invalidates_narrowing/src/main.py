from tpy import int32


def bad(x: int32 | None) -> int32:
    assert x is not None
    x = None
    return x + 1  # tpyc: warning(/Potential None access/)
