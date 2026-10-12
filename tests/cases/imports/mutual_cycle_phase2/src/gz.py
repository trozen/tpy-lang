from tpy import int32
import gy


def total(rows: list[list[int32]]) -> int32:
    return sum(gy.size(r) for r in rows)


def one() -> int32:
    return 1
