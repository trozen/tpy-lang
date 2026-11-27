# Side-effecting argument sources the sections share: each prints when it runs.
from tpy import int32


def mark(tag: str) -> str:
    print(tag, "evaluated")
    return tag


def mark_i(tag: str, n: int32) -> int32:
    print(tag, "evaluated")
    return n
