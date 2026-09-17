# The imported half of the view-demotion case: one writer, one non-writer that
# still declares a mutable parameter, and the `readonly` twin. The caller's
# decision must turn on these SIGNATURES alone.
from tpy import Own, int32, readonly


class Outer:
    def __init__(self, name: Own[str]) -> None:
        self.name = name


def bump(xs: list[str]) -> None:
    xs[0] = "imported"


def touch(xs: list[str]) -> int32:
    return len(xs)


def peek(xs: readonly[list[str]]) -> int32:
    return len(xs)


def peek_rec(o: readonly[Outer]) -> int32:
    return len(o.name)
