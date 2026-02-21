from tpy import Int32


def f(a: Int32 | None, b: Int32 | None) -> Int32:
    assert a is not None
    return a + b  # tpyc: warning(/Potential None access/)
