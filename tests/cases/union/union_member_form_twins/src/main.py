# A union reads in its holder's form whatever name it shadows: a
# comprehension variable over `list[Pet | int32]` hiding a borrow-form union
# parameter, and a recursive-alias wrapper passed to a protocol parameter.
from typing import Protocol

from tpy import int32


class Pet:
    def __init__(self, n: int32) -> None:
        self.n = n


def show(p: Pet | int32) -> str:
    if isinstance(p, Pet):
        return "pet" + str(p.n)
    return "int" + str(p)


def shadow_isinstance(x: Pet | int32, xs: list[Pet | int32]) -> None:
    # a. The comprehension's `x` is the stored element, not the parameter.
    print("a:", [isinstance(x, Pet) for x in xs])  # tpyc: ok
    print("a: param", isinstance(x, Pet))


def shadow_call(p: Pet | int32, xs: list[Pet | int32]) -> None:
    # b. The stored element passes to a read-only borrow parameter, lifted
    # from the storage form, not read as the hidden parameter.
    print("b:", [show(p) for p in xs])  # tpyc: ok
    print("b: param", show(p))


type Tree = int | list[Tree]


class Show(Protocol):
    pass


def show_tree(x: Show) -> int:
    return 1


def tree_param(t: Tree) -> int:
    # c. A recursive-alias wrapper parameter passes to a protocol as itself.
    return show_tree(t)  # tpyc: ok


def tree_len(t: Tree) -> int:
    if isinstance(t, list):
        return len(t)
    return -1


def tree_local() -> int:
    t: Tree = [1, [2]]
    # c. A recursive-alias wrapper local passes to a protocol as itself.
    n = show_tree(t)  # tpyc: ok
    if isinstance(t, list):
        t.append(3)
        print("c: local", len(t))
    return n


def main() -> None:
    pet = Pet(1)
    xs: list[Pet | int32] = [Pet(2), 3]
    shadow_isinstance(pet, xs)
    shadow_call(pet, xs)
    t: Tree = [1]
    n = tree_param(t) + tree_local()
    print("c: both", n)
    # c. The caller's tree, grown after the protocol call, reads back grown
    # through a parameter bound to the same object.
    if isinstance(t, list):
        t.append(5)
    print("c: caller", tree_len(t))


main()
