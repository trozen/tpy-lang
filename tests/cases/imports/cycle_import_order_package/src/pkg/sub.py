from pkg import X  # tpyc: ok


def grow(x: X) -> None:
    x.v += 1
