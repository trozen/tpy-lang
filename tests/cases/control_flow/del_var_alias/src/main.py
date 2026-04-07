# del on a reference alias (T&) must not destroy the source object
from tpy import Own

class Obj:
    val: int
    def __init__(self, val: int) -> None:
        self.val = val

def make() -> Own[Obj]:
    return Obj(42)

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

main()
