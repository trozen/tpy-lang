# Sibling of wrapped_own_param_move for the constructor member-init list: a
# Send[Own[T]] ctor param moved into a field. The MIL move-eligibility set also
# has to peel the transparent Send/Sync marker, else a @nocopy field store would
# reject as a copy. @nocopy Token proves the move (a copy would not compile).
from tpy import Own, Send, Int32, nocopy
from typing import Protocol


class Counted(Protocol):
    def value(self) -> Int32: ...


@nocopy
class Token(Counted):
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def value(self) -> Int32:
        return self.n


@nocopy
class Holder[T: Counted]:
    item: T

    def __init__(self, item: Send[Own[T]]) -> None:
        self.item = item   # tpyc: ok -- Send[Own[T]] param moved into the field

    def value(self) -> Int32:
        return self.item.value()


def main() -> None:
    h = Holder[Token](Token(7))
    print("held:", h.value())


main()
