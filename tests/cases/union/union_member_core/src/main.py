# A value of one union member is converted at that member's own slot, as
# its scalar twin is there, then built into the union: each section sits
# beside its twin and prints what CPython prints for both.
from tpy import Own, int32, int64, uint32, uint64, StrView
from tplib.box import Box
from part_params import main as params_main
from part_stores import main as stores_main
from part_kinds import main as kinds_main
from part_values import main as values_main
from part_faces import main as faces_main
from part_ctor import main as ctor_main
from part_tuples import main as tuples_main
from part_moves import main as moves_main


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Leaf(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    m: int32

    def __init__(self) -> None:
        self.m = 0


# return: a covariant Box passes through to its member, as the twin's
# `return b` does (moved implicitly)
def box_union() -> Own[Box[Base] | int32]:
    b = Box(Leaf(1))
    return b  # tpyc: ok


def box_twin() -> Own[Box[Base]]:
    b = Box(Leaf(2))
    return b  # tpyc: ok


# return: a narrowed Optional's payload is copied into the member, as the
# twin `-> Own[A]` copies it, warned (`return A((*x));`; the twin is not
# here: its MIR lowering raises,
# BUGS.md#mir-narrowed-optional-own-return-raises). A declared copy: no
# write after it is compared with CPython.
def narrowed_union(x: A | None) -> Own[A | B]:
    if x is not None:
        return x  # tpyc: warning(/copies A/)
    return B()


# return: a named tuple's element is built at its member, as the scalar
# `return n` at `int64 | uint64` is (no caller consumes a tuple of unions
# yet, BUGS.md#value-union-tuple-element-reads-reject: the body's C++ is the
# subject, built by the exec phase)
def tuple_union(n: uint32) -> tuple[int64 | uint64]:
    v = (n,)
    return v  # tpyc: ok


def scalar_union(n: uint32) -> int64 | uint64:
    return n  # tpyc: ok


def show(tag: str, u: int64 | uint64) -> None:
    print(tag, u, isinstance(u, uint64))


# return: an owned str moves into its member, as the twin's does
def str_union(s: Own[str]) -> int32 | str:
    return s  # tpyc: ok


# return: a built String local converts into the `str` member by moving its
# buffer, as the twin's `return s;` does
def string_union(n: int32) -> str | int32:
    s = "abc" + str(n)
    return s  # tpyc: ok


def string_twin(n: int32) -> str:
    s = "abc" + str(n)
    return s  # tpyc: ok


# return: absence into a union holding None
def absent(flag: bool) -> int64 | uint64 | None:
    if flag:
        return 3
    return None  # tpyc: ok


# global: a module-level union takes a width as a local would (its C++
# initializer is the subject; a union global's read is not lowered yet)
g_w: int64 | uint64 = uint32(4)  # tpyc: ok


# field write: a view param is stored in the union's view member as the
# twin's view field stores it (a local buffer's view escaping either is
# MIR's, BUGS.md#record-view-field-escapes-local-buffer)
class Label:
    u: StrView | int32
    v: StrView

    def __init__(self) -> None:
        self.u = 0
        self.v = ""

    def mark(self, s: StrView) -> None:
        self.u = s  # tpyc: ok
        self.v = s  # tpyc: ok


def main() -> None:
    u = box_union()
    if isinstance(u, Box):
        print("box", u.get().n, box_twin().get().n)
    a = A(3)
    r = narrowed_union(a)
    if isinstance(r, A):
        print("narrowed", r.n)
    show("scalar", scalar_union(6))
    sv = str_union("abc")
    print("str", isinstance(sv, str))
    st = string_union(1)
    if isinstance(st, str):
        print("string", st, string_twin(2))
    x = absent(True)
    y = absent(False)
    print("absent", x is None, y is None)
    lb = Label()
    lb.mark("view")
    print("label", lb.u, lb.v)
    params_main()
    stores_main()
    kinds_main()
    values_main()
    faces_main()
    ctor_main()
    tuples_main()
    moves_main()


main()
