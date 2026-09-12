# Guards the reachability claim behind the widened "the receiver is owned"
# fact: the owned-tuple unpack consume (`a, b = <owned name>`) reads that same
# fact, but a receiver can never reach it -- `self` is the record's nominal
# type, and the unpack rejects non-tuples three statements earlier. If a future
# change ever lets a receiver type as a tuple, this case stops rejecting and
# that consume path has to be revisited.
from typing import Self
from tpy import int32, Own


class Pair:
    a: int32
    b: int32

    def __init__(self, a: int32, b: int32) -> None:
        self.a = a
        self.b = b

    def split(self: Own[Self]) -> int32:
        x, y = self  # tpyc: error(/Cannot unpack non-tuple type/)
        return x + y


def main() -> None:
    print(Pair(1, 2).split())


main()
