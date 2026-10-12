from tpy import int32
import ra


class Counter:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


def count(xs: list[int32]) -> int32:
    return len(xs)


def push(xs: list[int32]) -> None:
    xs.append(ra.one())
