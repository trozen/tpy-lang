# `return self` in a consuming method under a `finally`: the receiver takes the
# finally-deferred capture like any owned local, so the finally runs BEFORE the
# move and its mutation of `self` is in the object the caller gets -- which is
# what CPython does (it hands back the very object). An eager move at the
# return line would relocate the value first and lose the mutation, so the
# `stamped()` leg observes it after the boundary. The `@nocopy` leg proves the
# deferral still MOVES rather than copies.
from typing import Self
from tpy import int32, Own, nocopy


class Ticket:
    id: int32

    def __init__(self, id: int32) -> None:
        self.id = id

    # The subject: the finally mutates the receiver the return hands back.
    def stamped(self: Own[Self]) -> Own[Self]:
        try:
            return self  # tpyc: ok
        finally:
            self.id += 100

    # The same shape with a finally that never touches the receiver.
    def logged(self: Own[Self]) -> Own[Self]:
        try:
            return self  # tpyc: ok
        finally:
            print("logged")


@nocopy
class Badge:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # A copy here cannot compile, so the deferred capture must be a move.
    def bumped(self: Own[Self]) -> Own[Self]:
        try:
            return self  # tpyc: ok
        finally:
            self.n += 1


def main() -> None:
    t = Ticket(7).stamped()
    # The finally's mutation is visible in what the call handed back.
    print(t.id)
    print(Ticket(1).logged().id)
    print(Badge(4).bumped().n)


main()
