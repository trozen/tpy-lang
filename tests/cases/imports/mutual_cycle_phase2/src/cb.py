from tpy import int32
import ca


def count(xs: list[int32]) -> int32:
    return len(xs)


def back(xs: list[int32]) -> int32:
    return ca.one() + count(xs)
