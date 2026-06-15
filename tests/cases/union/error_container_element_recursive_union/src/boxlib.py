from tpy import Int32

type Box = Int32 | str | list[Box]


def sink(xs: list[Box]) -> Int32:
    return 0
