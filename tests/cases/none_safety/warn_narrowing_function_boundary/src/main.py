from tpy import int32

x: int32 | None = None


def prove() -> None:
    assert x is not None


def use() -> int32:
    return x + 1  # tpyc: warning(/Potential None access/)
