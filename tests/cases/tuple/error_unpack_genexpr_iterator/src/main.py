# An unpacking for head over a generator expression of a list is refused: the
# expression's step copies the stored tuple, so `a` would not refer to the
# list's A (BUGS.md#combinator-tuple-step-copies-record-element).
from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def bump(xs: list[tuple[A, int32]]) -> None:
    # the refused head; iterate the list itself (`for a, n in xs`)
    for a, n in (t for t in xs):  # tpyc: error(/not yet supported by C\+\+ code generation \(expr\.genexpr\)/)
        a.v += n


def main() -> None:
    xs = [(A(1), 10)]
    bump(xs)
    print(xs[0][0].v)


main()
