# A relayed borrow-tuple call: the callee's return borrows its receiver
# (return_borrows_from), so returning the call result rooted in a LOCAL
# receiver must be rejected -- the pointers die with the local.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


class Holder:
    box: Box
    n: Int32
    def __init__(self, b: Box, n: Int32) -> None:
        self.box = b
        self.n = n
    def get_pair(self) -> tuple[Box, Int32]:
        return (self.box, self.n)


def relay_from_local(b: Box) -> tuple[Box, Int32]:
    local_h = Holder(b, 42)
    return local_h.get_pair()  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
