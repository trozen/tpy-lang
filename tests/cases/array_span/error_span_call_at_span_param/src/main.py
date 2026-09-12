# A `span()` rvalue passed straight into a Span parameter: that argument slot has
# no lowering row, so the call keeps rejecting.
from tpy import int32, Span, readonly, span


def total(sp: Span[readonly[int32]]) -> int32:
    return len(sp)


def main() -> None:
    items: list[int32] = [1, 2, 3]
    print(total(span(items)))  # tpyc: error(/call.arg_shape.span/)


main()
