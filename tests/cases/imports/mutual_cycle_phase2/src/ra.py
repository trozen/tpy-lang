# Receivers across a cycle, the peer (rb) analyzed LATER: a parameter
# calling a read-only method of rb's class, a method handing a field to rb's
# reading function, and the inverse -- a forward to rb's writing function.
from tpy import int32
import rb


# Only reads through `c`: a const reference.
def peek(c: rb.Counter) -> int32:
    return c.get()


class Holder:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    # Hands `self.xs` only to rb.count, which reads: a const method.
    def total(self) -> int32:
        return rb.count(self.xs) + 1


# rb.push appends: `xs` stays a mutable reference.
def fill(xs: list[int32]) -> None:
    rb.push(xs)


def one() -> int32:
    return 1
