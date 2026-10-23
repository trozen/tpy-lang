# A ValueType record may hold an Optional of an all-value tuple, or of a
# ValueType-bounded type parameter; the copy is unobservable (union field:
# BUGS.md#scalar-tuple-union-field-unlowered).
from tpy import int32, ValueType


class OptPair(ValueType):
    t: tuple[int32, str] | None  # tpyc: ok

    def __init__(self, t: tuple[int32, str] | None) -> None:
        self.t = t


def show(tag: str, v: OptPair) -> None:
    if v.t is None:
        print(tag, "none")
    else:
        print(tag, v.t[0], v.t[1])


# free function
def free_fn() -> None:
    v = OptPair((1, "a"))
    w = v
    v = OptPair(None)
    show("free v", v)
    show("free w", w)


class Keeper:
    v: OptPair

    def __init__(self) -> None:
        self.v = OptPair((4, "c"))

    # method body
    def run(self) -> None:
        w = self.v
        self.v = OptPair((6, "d"))
        show("method v", self.v)
        show("method w", w)


class Slot[T: ValueType](ValueType):
    x: T | None  # tpyc: ok

    def __init__(self, x: T | None) -> None:
        self.x = x


class P(ValueType):
    def __init__(self, n: int32) -> None:
        self.n = n

    def __str__(self) -> str:
        return "P(" + str(self.n) + ")"


def show_slot[T: ValueType](tag: str, s: Slot[T]) -> None:
    if s.x is None:
        print(tag, "none")
    else:
        print(tag, s.x)


# bounded type parameter: the copy keeps the old value at either instantiation
def bounded() -> None:
    v = Slot(1)
    w = v
    v = Slot[int32](None)
    show_slot("bounded int v", v)
    show_slot("bounded int w", w)
    p = Slot(P(2))
    q = p
    p = Slot(P(3))
    show_slot("bounded rec p", p)
    show_slot("bounded rec q", q)


def main() -> None:
    free_fn()
    Keeper().run()
    bounded()


main()

# module-level statement
mv = OptPair((8, "f"))
mw = mv
mv = OptPair(None)
show("module v", mv)
show("module w", mw)
