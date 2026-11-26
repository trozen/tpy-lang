# A borrow-returning @property read off a TEMPORARY receiver compiles where it
# is safe, as its method spelling does; here the getter lends a GLOBAL.
from tpy import Own, int32

import part_copies
import part_global
import part_loops
import part_warn

G = [1, 2]


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Rec:
    sub: Box

    def __init__(self) -> None:
        self.sub = Box(5)


GREC = Rec()


class H:
    tag: int32

    def __init__(self) -> None:
        self.tag = 0

    @property
    def items(self) -> list[int32]:
        return G

    @property
    def gch(self) -> Rec:
        return GREC


def mk() -> Own[H]:
    return H()


# module global slot: the slot aliases G.
SLOT: list[int32] = mk().items  # tpyc: ok
SLOT.append(3)
print("global_slot:", len(G))


def borrow() -> list[int32]:
    # return: the caller's reference aliases G.
    return mk().items  # tpyc: ok


def decl() -> None:
    # local decl
    v = mk().items  # tpyc: ok
    v.append(4)
    print("decl:", len(G))


def hoisted_decl() -> None:
    try:
        # branch-first decl: read after the `try`, so predeclared above it
        v = mk().items  # tpyc: ok
    except ValueError:
        return
    v.append(5)
    print("hoisted_decl:", len(G))


def field_read() -> None:
    # field read through the getter
    y = mk().gch.sub  # tpyc: ok
    y.n = 99
    print("field_read:", GREC.sub.n)


def main() -> None:
    decl()
    hoisted_decl()
    b = borrow()
    b.append(6)
    print("return:", len(G))
    field_read()
    part_loops.main()
    part_copies.main()
    part_global.main()
    part_warn.main()


main()
