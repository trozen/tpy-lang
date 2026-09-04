# A record ternary in a constructor's member-init list where one arm is a
# ctor prvalue and the other a plain name -- the C++ `?:` stays a prvalue.
# A named arm stores a COPY where CPython aliases, and the compiler warns
# about it exactly as it does for a plain `self.f = name` store; the two
# annotated lines are the pin.
from tpy import copy


class V3:
    x: float

    def __init__(self, x_: float) -> None:
        self.x = x_


class Material:
    color: V3
    emission: V3

    def __init__(self, color: V3, emission: V3 | None = None) -> None:
        self.color = copy(color)
        # The subject: a ctor rvalue arm beside a narrowed-Optional NAME arm.
        self.emission = V3(0.0) if emission is None else emission  # tpyc: warning(/copies .* into field/)


class Layer:
    base: V3

    def __init__(self, given: V3, use_given: bool) -> None:
        # The same shape with a plain (never-Optional) name arm.
        self.base = given if use_given else V3(9.0)  # tpyc: warning(/copies .* into field/)


def main() -> None:
    a = Material(V3(1.0))
    print(a.color.x, a.emission.x)
    b = Material(V3(2.0), V3(7.0))
    print(b.color.x, b.emission.x)
    c = Layer(V3(3.0), True)
    d = Layer(V3(4.0), False)
    print(c.base.x, d.base.x)


main()
