# A constructor argument at a FLUSH-LESS nested position -- the one reached
# through an argument-temp recursion, here the `Own[@dynamic P]` conformer
# inside a second owning constructor. That family held thirteen rows and
# refused every other source, including the bare NAME pass-through; its rule
# is that an admitted cell renders TEMP-FREE, which the rows added here do.
# The constructed object is read back afterwards, so a source that had been
# copied instead of bound would show up in the value.
from typing import Optional, Protocol
from tpy import dynamic, int32
from tplib import Box, Rc


@dynamic
class DynP(Protocol):
    def ping(self) -> int32: ...


class Rec:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x

    def ping(self) -> int32:
        return self.x


class W:
    items: list[int32]
    rec: Rec

    def __init__(self) -> None:
        self.items = [1, 2]
        self.rec = Rec(3)


class KList:
    tag: int32

    def __init__(self, p: list[int32]) -> None:
        self.tag = len(p)

    def ping(self) -> int32:
        return self.tag


class KMut:
    tag: int32

    def __init__(self, p: list[int32]) -> None:
        p.append(99)
        self.tag = len(p)

    def ping(self) -> int32:
        return self.tag


class KRec:
    tag: int32

    def __init__(self, p: Rec) -> None:
        self.tag = p.x

    def ping(self) -> int32:
        return self.tag


class KOpt:
    tag: int32

    def __init__(self, p: Optional[Rec]) -> None:
        self.tag = 0 if p is None else p.x

    def ping(self) -> int32:
        return self.tag


def main() -> None:
    w = W()
    xs = [7, 8, 9]
    # a bare container NAME at the nested constructor's slot
    rc1: Rc[Box[DynP]] = Rc.new(Box(KList(xs)))
    print("name", rc1.get().get().ping())
    # ... its container FIELD read twin
    rc2: Rc[Box[DynP]] = Rc.new(Box(KList(w.items)))
    print("field", rc2.get().get().ping())
    # a record FIELD read at a record slot
    rc3: Rc[Box[DynP]] = Rc.new(Box(KRec(w.rec)))
    print("rec_field", rc3.get().get().ping())
    # a container LITERAL at an unmutated slot, rendered in place
    rc4: Rc[Box[DynP]] = Rc.new(Box(KList([1, 2, 3, 4])))
    print("literal", rc4.get().get().ping())
    # a record NAME at a pointer-repr Optional slot -- the address-of lift
    r = Rec(5)
    rc5: Rc[Box[DynP]] = Rc.new(Box(KOpt(r)))
    print("opt_ptr", rc5.get().get().ping())
    # the same bare NAME at a MUTATED slot: the constructor appends through
    # its parameter, so a silent copy would leave the caller's list short
    ys = [7, 8]
    rc6: Rc[Box[DynP]] = Rc.new(Box(KMut(ys)))
    print("mutated", rc6.get().get().ping(), ys)


main()
