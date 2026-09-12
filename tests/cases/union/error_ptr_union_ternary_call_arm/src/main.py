# A ternary over union values whose else-arm is a CALL returning the union:
# not lowered yet, so the case pins the reject.
from tpy import int32


class Alpha:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Beta:
    y: int32

    def __init__(self, y: int32) -> None:
        self.y = y


def pick(q: Alpha | Beta) -> Alpha | Beta:
    return q


def bump(p: Alpha | Beta, q: Alpha | Beta, c: bool) -> None:
    # A ptr-variant-returning CALL arm is its own rung and is not admitted.
    t = p if c else pick(q)  # tpyc: error(/expr.ifexpr/)
    if isinstance(t, Alpha):
        t.x += 100


def main() -> None:
    p = Alpha(3)
    q = Beta(7)
    bump(p, q, True)
    print(p.x)


main()
