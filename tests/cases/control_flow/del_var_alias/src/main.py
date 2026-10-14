# del on a reference alias (T&) must not destroy the source object
from typing import Iterator

from tpy import Own

class Obj:
    val: int
    def __init__(self, val: int) -> None:
        self.val = val

def make() -> Own[Obj]:
    return Obj(42)

class Holder:
    obj: Obj

    def __init__(self) -> None:
        self.obj = Obj(3)


class Drop:
    val: int

    def __init__(self, val: int) -> None:
        self.val = val

    def __del__(self) -> None:
        print("drop", self.val)


# a generator's owning local is destroyed by its `del`, before what follows
def gen_owner_del() -> Iterator[int]:
    p = Drop(2)
    yield 0
    del p  # tpyc: ok
    print("after")
    yield 1


# a generator's alias of a field, deleted across a yield, leaves the field
def gen_field_alias() -> Iterator[int]:
    h = Holder()
    inner = h.obj
    yield 0
    del inner  # tpyc: ok
    h.obj.val += 1
    yield h.obj.val


def main() -> None:
    # del alias from constructor
    a = b = Obj(5)
    del a
    print(b.val)

    # del alias from function return
    c = d = make()
    del c
    print(d.val)

    # del alias, then reassign
    e = f = Obj(99)
    del e
    e = Obj(0)
    print(e.val, f.val)

    # regular alias (not multi-assign)
    g = Obj(7)
    h = g
    del h
    print(g.val)

    # del source that has alias: alias preserved
    i = j = Obj(50)
    del j
    print(i.val)

    # standalone owner: move-sink should be emitted
    standalone = Obj(100)
    print(standalone.val)
    del standalone

    for x in gen_field_alias():
        print("gen_field_alias", x)
    for x in gen_owner_del():
        print("gen_owner_del", x)

main()
