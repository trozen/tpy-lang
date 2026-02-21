from tpy import Int32

x: Int32 | None = None


def prove() -> None:
    assert x is not None


def use() -> Int32:
    return x + 1  # tpyc: warning(/Potential None access/)
