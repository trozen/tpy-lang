# The mirror of ra/rb: the class and the writer are in the member analyzed
# EARLIER (ry).
from tpy import int32


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


def count(xs: list[int32]) -> int32:
    return len(xs)


def push(xs: list[int32]) -> None:
    xs.append(rz.one())


# At the bottom so CPython, importing ry first, has Counter defined when
# rz's annotations evaluate it.
import rz  # noqa: E402  # tpyc: warning(/Import statement should be at the top/)
