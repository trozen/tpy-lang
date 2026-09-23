# A borrowed Optional member at an Own tuple element warns its copy but has no
# render yet (BUGS.md#optional-member-own-copy-no-render): a located reject.
from tpy import int32, Own


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def take(t: Own[tuple[P | None, P | None]]) -> int32:
    return 0


def f(o: P | None) -> int32:
    # The Optional parameter is copied into both owned elements.
    return take((o, o))  # tpyc: warning(/tuple element 0\)/) warning(/tuple element 1\)/) error(/expr.tuple_literal/)


def main() -> None:
    print(f(P(1)))


main()
