# A nested def inside a resumable frame is emitted as a MEMBER of the frame
# struct, and a member is a contiguous function with a top of its own -- so a
# body needing a function-top hoist (an OWN rebind slot, a kept `with`
# manager, a scope-escape hoist, a @dynamic rebind) compiles there exactly as
# it does in a plain function. The slots are the member's own locals, so two
# calls of the member never share one.
from typing import Iterator, Protocol
from tpy import int32, dynamic
import asyncio


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    m: Rec

    def __init__(self, tag: int32) -> None:
        self.m = Rec(tag)


class RCM:
    r: Rec

    def __init__(self, x: int32) -> None:
        self.r = Rec(x)

    def __enter__(self) -> Rec:
        return self.r

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


@dynamic
class Shape(Protocol):
    def area(self) -> int32: ...


class Sq:
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s

    def area(self) -> int32:
        return self.s * self.s


class Tri:
    b: int32

    def __init__(self, b: int32) -> None:
        self.b = b

    def area(self) -> int32:
        return self.b * 2


# free generator: a rebind across try/except needs an OWN slot, and the two
# calls in one resume must not share it
def gen_own_slot() -> Iterator[int32]:
    fl = Rec(11)

    def inner(k: int32) -> int32:  # tpyc: ok
        try:
            r = Rec(k)
        except ValueError:
            r = Rec(5)
        return r.x + fl.x

    yield inner(4) + inner(9)
    yield inner(1)


# free generator: a loop-local lent past its loop is hoisted, so `holder` and
# `b` share storage -- the in-loop mutation observes it
def gen_escape_hoist() -> Iterator[int32]:
    def inner() -> int32:
        holder = Rec(0)
        for q in range(3):
            b = B(q)
            holder = b.m  # tpyc: warning(/will not keep the object it was given/)
            holder.x += 10
            print("escape in-loop:", b.m.x)
        return holder.x

    yield inner()


# generator method: a kept `with` manager hoists to the member's top, so the
# target still aliases live storage after the block
class Holder:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def gen(self) -> Iterator[int32]:
        def inner() -> int32:  # tpyc: ok
            with RCM(1) as rr:
                rr.x += 1
            with RCM(2) as rr:
                rr.x += 1
            return rr.x

        yield inner() + self.n


# async def: a @dynamic rebind allocates a fresh erased slot per reseat
async def coro_dyn() -> int32:
    fl = Rec(7)

    def inner() -> int32:  # tpyc: ok
        s: Shape = Sq(3)
        v = s.area()
        s = Tri(5)
        return v + s.area()

    await asyncio.sleep(0)
    return inner() + fl.x


# generator whose yield sits in a loop: the member is called once per resume
def gen_per_resume() -> Iterator[int32]:
    fl = Rec(100)

    def inner(k: int32) -> int32:  # tpyc: ok
        try:
            r = Rec(k)
        except ValueError:
            r = Rec(5)
        return r.x

    for i in range(3):
        yield inner(i) + fl.x


def main() -> None:
    for v in gen_own_slot():
        print("own_slot:", v)
    for v in gen_escape_hoist():
        print("escape:", v)
    h = Holder()
    for v in h.gen():
        print("with:", v)
    print("dyn:", asyncio.run(coro_dyn()))
    for v in gen_per_resume():
        print("per_resume:", v)


main()
