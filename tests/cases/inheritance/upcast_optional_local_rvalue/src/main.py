# A SUBCLASS rvalue at a pointer-repr `Optional[Base]` local: the owning slot
# spells the base, so the init slices -- `Base __slot_N = Child();` -- exactly
# as the same upcast does at a field or a return. The slice is the right render
# because the slot has to be one concrete size and the source is a temporary
# nothing else holds; it is declared, not silent, since sema warns "upcast
# narrows" on every line below, and the sections read only Base-visible state.
import asyncio
from tpy import int32
from typing import Iterator, Optional


class Base:
    n: int32

    def __init__(self) -> None:
        self.n = 1

    def tag(self) -> int32:
        return self.n


class Child(Base):
    def __init__(self) -> None:
        super().__init__()
        self.n = 2


class Holder:
    seed: int32

    def __init__(self, seed: int32) -> None:
        self.seed = seed

    # method position
    def read(self) -> int32:
        b: Optional[Base] = Child()  # tpyc: warning(/upcast narrows/)
        if b is None:
            return -1
        return b.tag() + self.seed


# free function: the decl form
def decl_position() -> int32:
    b: Optional[Base] = Child()  # tpyc: warning(/upcast narrows/)
    if b is None:
        return -1
    return b.tag()


# free function: the RESEAT form -- a None-init slot rebound to the subclass
def reseat_position(c: bool) -> int32:
    b: Optional[Base] = None
    if c:
        b = Child()  # tpyc: warning(/upcast narrows/)
    if b is None:
        return -1
    return b.tag()


# generator: the same slot inside a resumable body
def gen() -> Iterator[int32]:
    b: Optional[Base] = Child()  # tpyc: warning(/upcast narrows/)
    if b is not None:
        yield b.tag()
    yield 9


# async: the coroutine sibling
async def coro() -> int32:
    b: Optional[Base] = Child()  # tpyc: warning(/upcast narrows/)
    await asyncio.sleep(0)
    if b is None:
        return -1
    return b.tag()


def main() -> None:
    print("decl:", decl_position())
    print("method:", Holder(10).read())
    print("reseat:", reseat_position(True), reseat_position(False))
    print("gen:", end=" ")
    for v in gen():
        print(v, end=" ")
    print()
    print("async:", asyncio.run(coro()))


main()
