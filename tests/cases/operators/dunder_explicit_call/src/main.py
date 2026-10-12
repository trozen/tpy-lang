# An EXPLICITLY spelled dunder call on a user record -- the `__ne__` that
# delegates to `__eq__` is the idiom. Sema stamps every user dunder with its
# C++ operator template, so the call renders as that operator over the bare
# receiver rather than as a member call.
from __future__ import annotations
from tpy import int32


class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, other: str) -> bool:
        return self.n == len(other)

    def __ne__(self, other: str) -> bool:
        return not self.__eq__(other)  # tpyc: ok -- dunder call on `self`


class V:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __eq__(self, o: V) -> bool:
        return self.n == o.n


# A record argument that is a pointer-bound local (rebound in a branch) reads
# through its deref inside the operator; it aliases the list element, so the
# write through the list after the binding is what the operator sees.
def record_arg(a: V, vs: list[V], c: bool) -> bool:
    q = vs[0]
    if c:
        q = vs[1]
    vs[0].n += 1
    return a.__eq__(q)  # tpyc: ok


def probe(t: Tag, s: str) -> bool:
    return t.__ne__(s)  # tpyc: ok -- ... and on a record PARAM receiver


def main() -> None:
    t = Tag(3)
    three = "abc"
    two = "xy"
    # `__ne__` must be the run-time negation of `__eq__` on the same input.
    print(t.__eq__(three), t.__eq__(two))
    print(t.__ne__(three), probe(t, two))
    vs = [V(1), V(2)]
    print(record_arg(V(2), vs, True), record_arg(V(3), vs, False))


main()
