# A var first declared in a `with` body and read after the statement predeclares
# at the with. Two widenings: the with may itself sit in a branch or loop body,
# and a non-value name that is later rvalue-reassigned rides the pointer predecl
# with its own function-top slot. Where a body draws both that reseat slot and a
# kept manager's (kept_manager_and_reseat), each draws its own function-top slot
# and the two are numbered in source order -- the manager first, the reseat
# second.
from tpy import int32, Own


class CM:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> int32:
        return self.n

    def __exit__(self, et, ev, tb) -> None:
        print("exit", self.n)


def in_branch(n: int32) -> int32:
    if n > 0:
        with CM(n) as c:
            # First decl of `v`, read after the with: predeclared inside the
            # branch, where the C++ scope matches.
            v = c + 1
        print(v)
    return n


def in_loop() -> None:
    for i in range(2):
        with CM(i) as c:
            w = c * 2
        print(w)


def nonvalue_rvalue_reassigned(n: int32) -> int32:
    with CM(n) as c:
        # A list first-declared in the body and later rvalue-reassigned: the
        # pointer predecl plus a lazily allocated function-top slot.
        xs = [c]
    # Mutation after the block is visible, so the name still aliases the slot.
    xs.append(9)
    print(xs)
    xs = [n, n, n]
    return len(xs)


def nonvalue_in_branch(n: int32) -> int32:
    total = 0
    if n > 0:
        with CM(n) as c:
            ys = [c, c]
        ys.append(3)
        total = len(ys)
    return total


class Node:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> "Node":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass

    def next(self) -> Own["Node"]:
        return Node(self.n + 1)


def kept_manager_and_reseat() -> int32:
    with Node(1) as outer:
        with Node(10) as inner:
            print(inner.n)
        # An rvalue reseat of the hoisted pointer name, alongside the kept
        # manager of the inner with: two function-top slots.
        inner = inner.next()
        print(outer.n)
    return inner.n


def main() -> None:
    print(in_branch(2))
    in_loop()
    print(nonvalue_rvalue_reassigned(5))
    print(nonvalue_in_branch(1))
    print(kept_manager_and_reseat())


main()
