# A whole-subject capture of a POINTER-repr `Optional[record]` (`case v:`
# after a class arm) binds the Optional itself, so the capture is still
# nullable: a proven read derefs bare, an unproven one draws the null check.
# Each section mutates through the capture and the caller observes it, so a
# silent copy of the pointee cannot pass.
from tpy import int32
from typing import Optional, Iterator
import asyncio


class Cat:
    lives: int32

    def __init__(self, lives: int32) -> None:
        self.lives = lives


# free function: the capture is tested for None, then read and mutated
def free_fn(o: Optional[Cat]) -> int32:
    match o:  # tpyc: ok
        case Cat(lives=1):
            return -1
        case v:
            if v is None:
                return 0
            v.lives += 100
            return v.lives


# free function: the capture is read with NO None proof -- the null check
def unproven(o: Optional[Cat]) -> int32:
    match o:
        case Cat(lives=1):
            return -1
        case v:
            return v.lives  # tpyc: warning(/Potential None access/)


# free function: `case w as v:` over the same subject still rejects, so the
# capture arm here is the plain one after an explicit `case None:`
def after_none(o: Optional[Cat]) -> int32:
    match o:  # tpyc: ok
        case None:
            return 0
        case v:
            v.lives += 100
            return v.lives


class Holder:
    seen: int32

    # constructor: the capture binds during __init__
    def __init__(self, o: Optional[Cat]) -> None:
        self.seen = 0
        match o:  # tpyc: ok
            case Cat(lives=1):
                self.seen = -1
            case v:
                if v is not None:
                    v.lives += 100
                    self.seen = v.lives

    # method: the capture binds off a parameter of a method
    def bump(self, o: Optional[Cat]) -> int32:
        match o:  # tpyc: ok
            case Cat(lives=1):
                return -1
            case v:
                if v is None:
                    return 0
                v.lives += 100
                return v.lives


# async def: the arm does not suspend, so the match lowers on the sync route
async def a_bump(o: Optional[Cat]) -> int32:
    match o:  # tpyc: ok
        case Cat(lives=1):
            return -1
        case v:
            if v is None:
                return 0
            v.lives += 100
            return v.lives


# context-manager-free block positions: a `with`-less try/finally arm body
def in_try(o: Optional[Cat]) -> int32:
    total = 0
    try:
        match o:  # tpyc: ok
            case Cat(lives=1):
                total = -1
            case v:
                if v is not None:
                    v.lives += 100
                    total = v.lives
    finally:
        total += 1
    return total


# field source: the subject is an Optional FIELD, lifted with optional_to_ptr
class Box:
    pet: Optional[Cat]

    def __init__(self, pet: Optional[Cat]) -> None:
        # the parameter is a borrowed `Cat*`, the field an owning slot
        self.pet = pet  # tpyc: warning(/copies .* into field/)


def field_src(b: Box) -> int32:
    match b.pet:  # tpyc: ok
        case Cat(lives=1):
            return -1
        case v:
            if v is None:
                return 0
            v.lives += 100
            return v.lives


async def amain(c: Cat) -> None:
    print("async", await a_bump(c), c.lives)


def main() -> None:
    a = Cat(2)
    print("free_fn", free_fn(a), a.lives)
    print("free_fn_none", free_fn(None))
    print("unproven", unproven(Cat(3)))
    b = Cat(4)
    print("after_none", after_none(b), b.lives)
    print("after_none_none", after_none(None))
    c = Cat(5)
    print("ctor", Holder(c).seen, c.lives)
    d = Cat(6)
    holder = Holder(None)
    print("method", holder.bump(d), d.lives)
    e = Cat(7)
    asyncio.run(amain(e))
    print("async_after", e.lives)
    f = Cat(8)
    print("in_try", in_try(f), f.lives)
    # observed through the FIELD's own storage: the ctor copies the record
    # into the field (the warned copy-into-owned-storage), so the outer name
    # would not see the capture's write
    box = Box(Cat(9))
    r = field_src(box)
    stored = 0
    p = box.pet
    if p is not None:
        stored = p.lives
    print("field_src", r, stored)


main()
