# Pointer-variant union monostate write arms and field-to-field copies:
# `w = None` local decl/rebind, `h.u = None` field write, `return None`, and
# `dst.u = src.u` (a storage-to-storage copy). Union field assigns are silent
# copies in TPy (warned) where CPython aliases; this test only reads values
# afterwards, so copy semantics are intended at those boundaries.
from tpy import Int32


class A:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32
    def __init__(self, y: Int32) -> None:
        self.y = y


class Holder:
    u: A | B | None
    def __init__(self, u: A | B | None) -> None:
        self.u = u  # tpyc: warning(/copies/)


def clear(h: Holder) -> None:
    h.u = None


def copy_field(dst: Holder, src: Holder) -> None:
    dst.u = src.u  # tpyc: warning(/copies/)


def cycle(v: A | B | None) -> A | B | None:
    w: A | B | None = None
    w = v
    w = None
    w = v
    return w


def pick_none() -> A | B | None:
    return None


def describe(h: Holder) -> str:
    u = h.u
    if u is None:
        return "none"
    if isinstance(u, A):
        return "a"
    return "b"


def main() -> None:
    a = A(7)
    b = B(9)
    start: A | B | None = a
    src = Holder(start)
    empty: A | B | None = None
    dst = Holder(empty)
    print(describe(src))
    print(describe(dst))
    copy_field(dst, src)
    print(describe(dst))
    alt: A | B | None = b
    src2 = Holder(alt)
    copy_field(dst, src2)
    print(describe(dst))
    clear(dst)
    print(describe(dst))
    got = cycle(start)
    if got is None:
        print("cycle lost it")
    else:
        print("cycle kept it")
    p = pick_none()
    if p is None:
        print("picked none")


main()
