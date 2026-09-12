from tpy import int32


def pick(flag: bool, a: int32 | None, b: int32 | None) -> int32:
    if flag:
        assert a is not None
    else:
        assert b is not None
    return a + 1  # tpyc: warning(/Potential None access/)
