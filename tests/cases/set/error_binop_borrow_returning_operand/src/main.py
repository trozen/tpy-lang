# A set binop whose left operand is a BORROW-returning call: the operand row is
# scoped to rvalue sources, so an lvalue whose render is the callee's own
# storage keeps rejecting.
from tpy import Int32


class Holder:
    items: set[Int32]

    def __init__(self) -> None:
        self.items = {1}


def borrow_set(h: Holder) -> set[Int32]:
    return h.items


def merged(h: Holder) -> None:
    c = borrow_set(h) | {7}  # tpyc: error(/binop.shape/)
    print(len(c))


def main() -> None:
    merged(Holder())


main()
