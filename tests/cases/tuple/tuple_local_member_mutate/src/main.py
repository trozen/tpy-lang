# A durable reference-member tuple used as a LOCAL: `t = (1, b)` aliases b
# (std::tuple<..., Box*>), so mutating through the tuple subscript reaches the
# member -- for both a local member and a borrowed param member (the param is
# inferred as a mutable borrow because its address escapes into the tuple).
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def mutate_param_member(b: Box) -> None:
    t = (1, b)
    t[1].val = 99


def local_member() -> int32:
    b = Box(5)
    t = (1, b)
    t[1].val = 77
    return b.val


def main() -> None:
    b = Box(5)
    mutate_param_member(b)
    print(b.val)
    print(local_member())


main()
