# A ValueType record may hold an Optional of an all-value tuple; the copy
# is unobservable (union field: BUGS.md#scalar-tuple-union-field-unlowered).
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


def main() -> None:
    free_fn()
    Keeper().run()


main()

# module-level statement
mv = OptPair((8, "f"))
mw = mv
mv = OptPair(None)
show("module v", mv)
show("module w", mw)
