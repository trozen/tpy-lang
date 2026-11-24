# Owned union storage: a field write, a member-init and a result take a
# member value as that member's own storage takes it -- a subclass's base
# part, a width, a view's owned copy, a copy of an object the caller keeps
# (warned, as the twin is).
from tpy import Own, int32, int64, uint32, uint64


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Sub(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Holder:
    u: Base | int32
    w: int64 | uint64
    s: int32 | str | None

    # member-init: an owned subclass object moves its base part in
    def __init__(self, b: Own[Sub], c: uint32, t: str) -> None:
        # the base member keeps only the base part, as the twin's field does
        self.u = b  # tpyc: warning(/upcast narrows/)
        self.w = c  # tpyc: ok
        self.s = t  # tpyc: ok

    # field write: the same members from a body
    def set(self, b: Own[Sub], c: uint32, t: str) -> None:
        # the base member keeps only the base part, as the twin's field does
        self.u = b  # tpyc: warning(/upcast narrows/)
        self.w = c  # tpyc: ok
        self.s = t  # tpyc: ok
        self.s = None  # tpyc: ok


# the scalar twin of `Holder`'s fields
class HolderTwin:
    u: Base
    w: int64
    s: str

    def __init__(self, b: Own[Sub], c: uint32, t: str) -> None:
        # a base field keeps only the base part
        self.u = b  # tpyc: warning(/upcast narrows/)
        self.w = c
        self.s = t

    def set(self, b: Own[Sub], c: uint32, t: str) -> None:
        # a base field keeps only the base part
        self.u = b  # tpyc: warning(/upcast narrows/)
        self.w = c
        self.s = t


class RBase:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]

    def __enter__(self) -> "RBase":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


# result: a context manager's target returned from its body is copied
# into the member, as the twin copies it (a declared, warned copy: no
# write after it is compared with CPython)
def with_union() -> Own[RBase | int32]:
    with RBase() as r:
        return r  # tpyc: warning(/copies RBase/)


def with_twin() -> Own[RBase]:
    with RBase() as r:
        return r  # tpyc: warning(/copies RBase/)


def main() -> None:
    h = Holder(Sub(1), 2, "a")
    u0 = h.u
    print("init", isinstance(u0, Base))
    h.set(Sub(3), 5, "bb")
    u = h.u
    if isinstance(u, Base):
        print("set", u.n)
    ht = HolderTwin(Sub(1), 2, "a")
    ht.set(Sub(3), 5, "bb")
    print("set-twin", ht.u.n, ht.w, ht.s)
    r = with_union()
    t = with_twin()
    if isinstance(r, RBase):
        r.items.append(2)
        print("with", len(r.items), len(t.items))
