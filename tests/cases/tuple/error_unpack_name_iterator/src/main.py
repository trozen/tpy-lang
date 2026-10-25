# An unpacking for head over a NAME bound to a container iterator is refused:
# its step copies the stored tuple, so `x` would not refer to the list's A
# (BUGS.md#combinator-tuple-step-copies-record-element).
from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def bump(xs: list[tuple[A, int32]]) -> None:
    it = iter(xs)
    # the refused head; iterating the list itself (`for x, n in xs`) aliases
    for x, n in it:  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt\.for_each:iter\.user_iterator\.name\)/)
        x.v += n


def main() -> None:
    xs = [(A(1), 10)]
    bump(xs)
    print(xs[0][0].v)


main()
