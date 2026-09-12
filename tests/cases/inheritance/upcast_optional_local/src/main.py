# A record lvalue upcast into every pointer slot (nullable/union locals, frame
# locals, reseats, args, returns) is an address bind: writes through the base
# handle are visible through the derived one; a FIELD is a warned copy.
import asyncio
from typing import Iterator


class Pet:
    name: str
    tag: int

    def __init__(self, name: str) -> None:
        self.name = name
        self.tag = 0

    def rename(self, name: str) -> None:
        self.name = name


class Dog(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


class Cat(Pet):
    def __init__(self, name: str) -> None:
        super().__init__(name)


# sync local: the address-of lift into the `Pet | None` slot
def upcast(d: Dog) -> None:
    p: Pet | None = d  # tpyc: warning(/upcast narrows/)
    if p is not None:
        p.rename("via-base")  # ... so this write is visible through `d`
    print("local", d.name)


# frame param: the same lift re-points the generator's frame field
def gen(d: Dog) -> Iterator[str]:
    p: Pet | None = d  # tpyc: warning(/upcast narrows/)
    yield d.name
    if p is not None:
        p.rename("frame-base")
    yield d.name


# reseat: a non-optional base local re-pointed at a sibling subclass
def reseat(d: Dog, c: Cat) -> None:
    p: Pet = d  # tpyc: warning(/upcast narrows/)
    p = c  # tpyc: warning(/upcast narrows/)
    p.rename("reseat-base")
    print("reseat", d.name, c.name)


# union: the subclass address binds the unique base member
def union(d: Dog) -> None:
    p: Pet | Cat = d  # tpyc: warning(/upcast narrows/)
    if isinstance(p, Pet):
        p.rename("union-base")
    print("union", d.name)


# union with None: same bind, a monostate alternative alongside
def union_opt(d: Dog) -> None:
    p: Pet | Cat | None = d  # tpyc: warning(/upcast narrows/)
    if isinstance(p, Pet):
        p.rename("union-opt-base")
    print("union_opt", d.name)


def take(p: Pet | None) -> None:
    if p is not None:
        p.rename("arg-base")


# call arg: `&(d)` at the nullable base param (unwarned, like a `Pet` param)
def arg(d: Dog) -> None:
    take(d)  # tpyc: ok
    print("arg", d.name)


def take_union(p: Pet | Cat) -> None:
    if isinstance(p, Pet):
        p.rename("arg-union-base")


def take_union_opt(p: Pet | Cat | None) -> None:
    if isinstance(p, Pet):
        p.rename("arg-union-opt-base")


# call arg union: the address lands in the unique base member of the param
def arg_union(d: Dog, e: Dog) -> None:
    take_union(d)  # tpyc: ok
    print("arg_union", d.name)
    take_union_opt(e)  # tpyc: ok
    print("arg_union", e.name)


def give(d: Dog) -> Pet | None:
    return d  # tpyc: warning(/upcast narrows/)


# return: the caller's handle aliases the argument
def ret(d: Dog) -> None:
    p = give(d)
    if p is not None:
        p.rename("ret-base")
    print("ret", d.name)


def give_union(d: Dog) -> Pet | Cat:
    return d  # tpyc: warning(/upcast narrows/)


def give_union_opt(d: Dog) -> Pet | Cat | None:
    return d  # tpyc: warning(/upcast narrows/)


# return union: the subclass address binds the unique base member of the
# returned variant, so the caller's handle aliases the argument
def ret_union(d: Dog, e: Dog) -> None:
    p = give_union(d)
    if isinstance(p, Pet):
        p.rename("ret-union-base")
    print("ret_union", d.name)
    q = give_union_opt(e)
    if isinstance(q, Pet):
        q.rename("ret-union-opt-base")
    print("ret_union", e.name)


# async: the lift re-points the coroutine's frame field, and the alias
# survives the suspension
async def coro(d: Dog) -> str:
    p: Pet | None = d  # tpyc: warning(/upcast narrows/)
    await asyncio.sleep(0)
    if p is not None:
        p.rename("async-base")
    return d.name


class Holder:
    p: Pet | None

    def __init__(self, d: Dog) -> None:
        self.p = d  # tpyc: warning(/copies Dog into field/) warning(/upcast narrows/)


# field: storage form, a warned copy -- read back through the field only
def field(d: Dog) -> None:
    h = Holder(d)
    if h.p is not None:
        h.p.rename("field-copy")
        print("field", h.p.name)


def gen_same(x: Pet) -> Iterator[str]:
    p: Pet | None = x  # tpyc: ok
    if p is not None:
        p.rename("same-frame")
    yield x.name


# same-type sources at every position stay unwarned
def same_type(x: Pet, y: Pet) -> None:
    p: Pet | None = x  # tpyc: ok
    q: Pet = x  # tpyc: ok
    q = y  # tpyc: ok
    u: Pet | Cat = x  # tpyc: ok
    take(y)  # tpyc: ok
    take_union(y)  # tpyc: ok
    if p is not None:
        p.rename("same-local")
    q.rename("same-reseat")
    if isinstance(u, Pet):
        u.rename("same-union")
    print("same", x.name, y.name)
    for n in gen_same(x):
        print("same", n)


def main() -> None:
    d = Dog("rex")
    upcast(d)
    print("local", d.name)
    for n in gen(Dog("gen")):
        print("frame", n)
    reseat(Dog("rd"), Cat("rc"))
    union(Dog("ud"))
    union_opt(Dog("uod"))
    arg(Dog("ad"))
    arg_union(Dog("aud"), Dog("aue"))
    ret(Dog("rt"))
    ret_union(Dog("rud"), Dog("rue"))
    print("async", asyncio.run(coro(Dog("ac"))))
    field(Dog("fd"))
    same_type(Pet("sx"), Pet("sy"))


main()
