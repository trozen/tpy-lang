# The sinks a container source reaches through the record-or-container gate,
# one section per position: a module-level list aliased into a local, a
# narrowed `Optional[list]` aliased into a local, and a container field
# written into another object's container field. The alias sections mutate
# through the binding and read the source back, so a copy would show; the
# field-write sections carry the copy warning their record twin carries and
# read back only through the written field (copy semantics are the rule
# there, as for `self.m = other.m`). A nested def returning the module-level
# list is left out on purpose: its caller binds a copy
# (BUGS.md#return-borrow-of-pending-callee-unrecorded).
from typing import Iterator, Optional

import asyncio

from tpy import int32

G: list[int32] = [7]


class Src:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1, 2]


class Holder:
    f: list[int32]

    def __init__(self) -> None:
        self.f = []

    # method: a narrowed Optional[list] aliased into a local
    def narrowed(self, o: Optional[list[int32]]) -> None:
        if o is not None:
            x = o  # tpyc: ok
            x.append(9)
        print("meth_narrowed_alias", len(o) if o is not None else -1)


class CtorWriter:
    f: list[int32]

    # constructor: a container field written into a container field
    def __init__(self, src: Src) -> None:
        self.f = []
        self.f = src.items  # tpyc: warning(/copies list.* into field/)
        self.f.append(9)
        print("ctor_field_write", len(self.f))


# free function: the module-level list aliased into a local
def global_alias() -> None:
    x = G  # tpyc: ok
    x.append(9)
    print("fn_global_alias", len(G))


# generator body: the field write, copy warned
def gen_field_write(src: Src) -> Iterator[int32]:
    h = Holder()
    h.f = src.items  # tpyc: warning(/copies list.* into field/)
    h.f.append(1)
    yield len(h.f)


# async body: the field write, copy warned
async def async_field_write(src: Src) -> int32:
    h = Holder()
    h.f = src.items  # tpyc: warning(/copies list.* into field/)
    h.f.append(1)
    return len(h.f)


# nested def: the narrowed alias inside a nested def
def nested_narrowed(o: Optional[list[int32]]) -> None:
    def inner() -> int32:
        if o is not None:
            x = o  # tpyc: ok
            x.append(9)
            return len(o)
        return -1
    print("nested_narrowed_alias", inner())


def main() -> None:
    global_alias()
    Holder().narrowed([1, 2])
    CtorWriter(Src())
    nested_narrowed([1])
    for v in gen_field_write(Src()):
        print("gen_field_write", v)
    print("async_field_write", asyncio.run(async_field_write(Src())))


main()
