# `self` stored into a tuple inside a sync method: it passes bare into the
# pointer slot, so the element aliases the receiver.
from tpy import Int32


class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def bump(self) -> Int32:
        # The tuple's second element is `self` itself.
        t = (1, self)
        t[1].val += 1
        return t[1].val


def main() -> None:
    b = Box(5)
    print(b.bump())
    # The write through the tuple element is visible on the receiver.
    print(b.val)


main()
