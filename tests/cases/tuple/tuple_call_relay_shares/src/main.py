# A relayed borrow-tuple call rooted in a durable receiver: the callee's
# return_borrows_from names the receiver, the relay forwards the borrow,
# and the caller's mutation through the tuple reaches the receiver's field.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    box: Box
    n: int32
    def __init__(self, b: Box, n: int32) -> None:
        self.box = b
        self.n = n
    def get_pair(self) -> tuple[Box, int32]:
        return (self.box, self.n)


def relay(h: Holder) -> tuple[Box, int32]:
    return h.get_pair()


def main() -> None:
    h = Holder(Box(5), 42)
    t = relay(h)
    t[0].val = 99
    print(h.box.val)
    print(t[1])


main()
