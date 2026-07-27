# The duplicability check walks a field access's receiver chain and sees
# through coercions, so a hidden call anywhere along the chain still binds a
# temp: a width-coerced property, a getter reached through an outer field
# read, and two getters in one chain each binding their own.
from tpy import Int8, Own

calls = 0


class Inner:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


class P:
    small: Int8

    def __init__(self) -> None:
        self.small = Int8(5)

    @property
    def narrow(self) -> Int8:
        global calls
        calls += 1
        return self.small

    @property
    def deep(self) -> Own[Inner]:
        global calls
        calls += 1
        return Inner(5)


def main() -> None:
    global calls
    p = P()

    # The Int8 result widens for the comparison, wrapping the property access
    # in a coercion the check has to see through.
    calls = 0
    coerced = 1 < p.narrow < 10
    print("coerced:", coerced, "calls:", calls)

    # The hidden call is the RECEIVER, not the outer node: `.v` is a plain
    # field, so only the recursive walk finds the getter underneath it.
    calls = 0
    nested = 1 < p.deep.v < 10
    print("nested receiver:", nested, "calls:", calls)

    # Two hidden calls in one chain, in different operand positions.
    calls = 0
    both = p.narrow < p.deep.v + 1 < 100
    print("two hidden calls:", both, "calls:", calls)

    # The needle side of membership walks the same chain.
    calls = 0
    member = p.deep.v in (5, 9)
    print("nested needle:", member, "calls:", calls)


main()
