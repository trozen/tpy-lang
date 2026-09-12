# The adjacent shape to the owned-container tuple return: a whole-tuple LOCAL
# as the return source. Only the tuple LITERAL spells the storage brace-init
# the slot needs, so the bound name keeps rejecting.
from tpy import int32, Own


def build() -> tuple[Own[list[int32]], Own[list[int32]]]:
    a: list[int32] = []
    b: list[int32] = []
    a.append(1)
    b.append(2)
    t = (a, b)
    return t  # tpyc: error(/return.tuple_source/)


def main() -> None:
    x, y = build()
    print(len(x), len(y))


main()
