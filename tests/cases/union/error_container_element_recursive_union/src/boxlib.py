from tpy import int32

type Box = int32 | str | list[Box]


def sink(xs: list[Box]) -> int32:
    return 0
