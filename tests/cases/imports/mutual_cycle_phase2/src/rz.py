from tpy import int32
import ry


# Only reads through `c`: a const reference.
def peek(c: ry.Counter) -> int32:
    return c.get()


class Holder:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    # Hands `self.xs` only to ry.count, which reads: a const method.
    def total(self) -> int32:
        return ry.count(self.xs) + 1


# ry.push appends: `xs` stays a mutable reference.
def fill(xs: list[int32]) -> None:
    ry.push(xs)


def one() -> int32:
    return 1
