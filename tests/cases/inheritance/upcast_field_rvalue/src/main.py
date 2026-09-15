# A subclass RVALUE written into a base-typed record FIELD, plain and
# Optional. A field is storage, so the assign slices to the base -- the copy
# sema's "upcast narrows" warning declares; the sibling shapes that BIND an
# address (locals, params, returns) alias instead and live in
# upcast_optional_local.
import asyncio
from typing import Iterator


class Pet:
    name: str

    def __init__(self, name: str) -> None:
        self.name = name

    def rename(self, name: str) -> None:
        self.name = name


class Dog(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


class Holder:
    plain: Pet
    opt: Pet | None

    def __init__(self) -> None:
        self.plain = Pet("init")
        self.opt = None

    # method: the same two writes off `self`
    def refill(self) -> None:
        self.plain = Dog("m-plain")  # tpyc: warning(/upcast narrows/)
        self.opt = Dog("m-opt")  # tpyc: warning(/upcast narrows/)


class CtorHolder:
    plain: Pet
    opt: Pet | None

    # constructor: the subclass rvalue reaches the member-init driver
    def __init__(self, tag: str) -> None:
        self.plain = Dog(tag)  # tpyc: warning(/upcast narrows/)
        self.opt = Dog(tag)  # tpyc: warning(/upcast narrows/)


def show(label: str, h: Holder) -> None:
    if h.opt is not None:
        print(label, h.plain.name, h.opt.name)


# free function: both field shapes, then a write through the base handle to
# show the field owns its own storage
def free_position() -> None:
    h = Holder()
    h.plain = Dog("f-plain")  # tpyc: warning(/upcast narrows/)
    h.opt = Dog("f-opt")  # tpyc: warning(/upcast narrows/)
    h.plain.rename("f-renamed")
    show("free", h)


def method_position() -> None:
    h = Holder()
    h.refill()
    show("method", h)


def ctor_position() -> None:
    c = CtorHolder("c")
    if c.opt is not None:
        print("ctor", c.plain.name, c.opt.name)


# generator: the write happens in a resumable body, across a suspension
def gen(h: Holder) -> Iterator[int]:
    h.plain = Dog("g-plain")  # tpyc: warning(/upcast narrows/)
    yield 1
    h.opt = Dog("g-opt")  # tpyc: warning(/upcast narrows/)
    yield 2


# async: the coroutine sibling of the generator body
async def coro(h: Holder) -> int:
    h.plain = Dog("a-plain")  # tpyc: warning(/upcast narrows/)
    await asyncio.sleep(0)
    h.opt = Dog("a-opt")  # tpyc: warning(/upcast narrows/)
    return 3


def main() -> None:
    free_position()
    method_position()
    ctor_position()
    gh = Holder()
    for s in gen(gh):
        print("gen", s)
    show("gen", gh)
    ah = Holder()
    print("async", asyncio.run(coro(ah)))
    show("async", ah)


main()
