# An EXPLICITLY spelled dunder call on a user record -- the `__ne__` that
# delegates to `__eq__` is the idiom. Sema stamps every user dunder with its
# C++ operator template, so the call renders as that operator over the bare
# receiver rather than as a member call.
from __future__ import annotations
from tpy import Int32


class Tag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __eq__(self, other: str) -> bool:
        return self.n == len(other)

    def __ne__(self, other: str) -> bool:
        return not self.__eq__(other)  # tpyc: ok -- dunder call on `self`


def probe(t: Tag, s: str) -> bool:
    return t.__ne__(s)  # tpyc: ok -- ... and on a record PARAM receiver


def main() -> None:
    t = Tag(3)
    three = "abc"
    two = "xy"
    # `__ne__` must be the run-time negation of `__eq__` on the same input.
    print(t.__eq__(three), t.__eq__(two))
    print(t.__ne__(three), probe(t, two))


main()
