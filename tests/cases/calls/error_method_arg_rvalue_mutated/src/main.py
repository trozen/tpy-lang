# A record call rvalue at a method slot the callee MUTATES: the method arg
# loop renders the rvalue inline and an rvalue cannot bind the `V3&` a
# mutated slot emits, so the shape must keep rejecting -- the same reason
# the ctor-rvalue row beside it is gated on the slot's const verdict.
from tpy import Own


class V3:
    x: float

    def __init__(self, x_: float) -> None:
        self.x = x_

    def muls(self, s: float) -> Own["V3"]:
        return V3(self.x * s)

    def absorb(self, v: "V3") -> None:
        v.x = 0.0
        self.x += 1.0


def main() -> None:
    a = V3(1.0)
    b = V3(2.0)
    a.absorb(b.muls(3.0))  # tpyc: error(/method.arg_shape/)
    print(a.x)


main()
