# A walrus at a @dynamic protocol parameter is refused, where the plain
# binding then the call compiles (BUGS.md#walrus-call-arg-reference-rejects).
from typing import Protocol
from tpy import deref, dynamic, int32
from tplib.rc import Rc


@dynamic
class Pet(Protocol):
    def bump(self) -> None: ...
    def count(self) -> int32: ...


class Dog(Pet):
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def bump(self) -> None:
        self.n += 1

    def count(self) -> int32:
        return self.n


def keep_dyn(p: Pet) -> None:
    p.bump()


def main() -> None:
    pets: Rc[Pet] = Rc.new(Dog())
    keep_dyn(d := deref(pets))  # tpyc: error(/call\.arg_shape\.protocol\.dyn/)
    print(d.count())


main()
