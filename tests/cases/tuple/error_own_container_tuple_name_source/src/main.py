# The adjacent shape to the owned-container tuple return: a whole-tuple LOCAL
# as the return source. Only the tuple LITERAL spells the storage brace-init
# the slot needs, so the bound name keeps rejecting.
from tpy import Int32, Own


def build() -> tuple[Own[list[Int32]], Own[list[Int32]]]:
    a: list[Int32] = []
    b: list[Int32] = []
    a.append(1)
    b.append(2)
    t = (a, b)
    return t  # tpyc: error(/return.tuple_source/)


def main() -> None:
    x, y = build()
    print(len(x), len(y))


main()
