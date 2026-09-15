# A local named `self` in a FREE function is an ordinary local: only a real
# receiver is durable by being called `self`, so returning it by reference is
# the same dangling-return error that error_return_shadow_global pins for any
# other name. Without that distinction the borrow is certified durable and the
# function hands back a reference to a dead frame slot.
from tpy import int32


class Holder:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def make() -> Holder:
    self = Holder(1)
    return self  # tpyc: error(/Cannot return local or temporary/)


def main() -> None:
    print(make().v)


main()
