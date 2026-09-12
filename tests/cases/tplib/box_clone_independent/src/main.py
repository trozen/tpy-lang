# Box.clone() hands back a Box that owns its OWN payload: mutating the source
# after the clone must not reach the copy. One section per payload shape; the
# Ptr-field section pins the other half of the rule -- a Ptr[T] field copies the
# POINTER, so its pointee stays shared.
from __future__ import annotations
from tpy import int32, Ptr, take_ptr
from tplib import Box


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class PtrHolder:
    p: Ptr[Cell]

    def __init__(self, p: Ptr[Cell]) -> None:
        self.p = p


# value payload: the clone is trivially independent.
def sec_value() -> None:
    b = Box[int32](1)
    c = b.clone()  # tpyc: ok
    b.set(99)
    print("value:", b.get(), c.get())


# reference payload: the motivating shape -- the clone deep-copies the Cell.
def sec_reference() -> None:
    b = Box[Cell](Cell(1))
    c = b.clone()  # tpyc: ok
    b.get().n = 99
    print("reference:", b.get().n, c.get().n)


def bump(p: Ptr[Cell]) -> None:
    p.n = 99


# a payload holding a Ptr field: the record is duplicated, the pointee is not.
def sec_ptr_field() -> None:
    target = Cell(1)
    b = Box[PtrHolder](PtrHolder(take_ptr(target)))
    c = b.clone()  # tpyc: ok
    live = b.get()
    bump(live.p)
    cloned = c.get()
    print("ptr-field:", live.p.n, cloned.p.n)


def main() -> None:
    sec_value()
    sec_reference()
    sec_ptr_field()


main()
