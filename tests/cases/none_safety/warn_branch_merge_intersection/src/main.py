from tpy import Int32


def pick(flag: bool, a: Int32 | None, b: Int32 | None) -> Int32:
    if flag:
        assert a is not None
    else:
        assert b is not None
    return a + 1  # tpyc: warning(/Potential None access/)
