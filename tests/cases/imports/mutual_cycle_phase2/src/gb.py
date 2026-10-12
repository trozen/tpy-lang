from tpy import int32
import ga


def size(xs: list[int32]) -> int32:
    return len(xs)


def back(xs: list[int32]) -> int32:
    return ga.one() + size(xs)
