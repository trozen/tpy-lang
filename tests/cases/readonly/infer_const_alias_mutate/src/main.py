# Auto-readonly must NOT mark a method const when it mutates self through a
# local alias of a self field. Two shapes:
#   local = self.field; local.mutating_method()
#   local = self.field; local.get().mutating_method()
# If wrongly inferred const, the C++ build fails (e.g. Box.get() on const
# self returns const Mutating&, can't dispatch the non-const protocol method).
from typing import Optional, Protocol
from tpy import int32, dynamic, Own
from tplib.box import Box


@dynamic
class Mutating(Protocol):
    def do_mutate(self) -> None: ...


class Inner:
    counter: int32

    def __init__(self) -> None:
        self.counter = 0

    def bump(self) -> None:
        self.counter += 1


class Impl:
    counter: int32

    def __init__(self) -> None:
        self.counter = 0

    def do_mutate(self) -> None:
        self.counter += 1


def make_box() -> Own[Box[Mutating]]:
    return Box(Impl())


class S:
    inner: Inner
    frame: Optional[Box[Mutating]]

    def __init__(self) -> None:
        self.inner = Inner()
        self.frame = make_box()

    # Direct call on a local alias of a self field -- must NOT be const.
    def mutate_via_alias(self) -> None:
        i = self.inner
        i.bump()

    # Chained call (Box.get().mutate()) on a local alias of a self field --
    # must NOT be const.
    def mutate_via_alias_chained(self) -> None:
        frame = self.frame
        if frame is not None:
            frame.get().do_mutate()


def main() -> None:
    s = S()
    s.mutate_via_alias()
    s.mutate_via_alias_chained()
    print(s.inner.counter)
    print("done")


main()
