# A `Span[readonly[T]]` combinator argument over reference elements rejects:
# the combinator spells the element `val_or_ref<const T>` and the loop var
# binds the raw proxy, which no member access can use (ill-formed on both
# paths); a plain `Span[T]` argument lends (tests/cases/list/comp_combinator_source).
from tpy import int32, Span, readonly


class Node:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    @readonly
    def val(self) -> int32:
        return self.v


def total(ns: Span[readonly[Node]], ys: list[int32]) -> int32:
    return sum([n.val() + y for n, y in zip(ns, ys)])  # tpyc: error(/expr\.list_comp/)


def main() -> None:
    us = [Node(1), Node(2)]
    print(total(us, [10, 20]))


main()
