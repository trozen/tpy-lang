from . import X  # tpyc: ok


def grow(x: X) -> None:
    x.v += 2
