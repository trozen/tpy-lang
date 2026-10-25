# An unpacking for head over a select of container iterators is refused: an
# arm's step copies the stored tuple, so `a` would not refer to the list's A
# (BUGS.md#combinator-tuple-step-copies-record-element).
from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def bump(xs: list[tuple[A, int32]], flag: bool) -> None:
    # the refused head; iterate the list itself (`for a, n in xs`)
    for a, n in (iter(xs) if flag else iter(xs)):  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt\.for_each:iter\.user_iterator\.if_expr\)/)
        a.v += n


def main() -> None:
    xs = [(A(1), 10)]
    bump(xs, True)
    print(xs[0][0].v)


main()
