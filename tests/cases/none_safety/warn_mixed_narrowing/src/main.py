from tpy import int32


def f(a: int32 | None, b: int32 | None) -> int32:
    assert a is not None
    return a + b  # tpyc: warning(/Potential None access/)
