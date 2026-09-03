# `case x as y` on a RECORD subject keeps rejecting: the record chain emits one
# binding line per arm, so admitting it would silently drop `x`
# (BUGS.md#match-as-capture-composite-tiers).
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def f(p: P) -> Int32:
    # Two whole-subject bindings off one arm, on the tier that binds once.
    match p:  # tpyc: error(/stmt\.match/)
        case a as b:
            return a.x + b.x


def main() -> None:
    print(f(P(1)))


main()
