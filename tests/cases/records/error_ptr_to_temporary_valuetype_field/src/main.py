# A pointer to a ValueType field read through a getter off a temporary is refused
# (docs/NATIVE_INTEROP.md, "What a binding keeps of its arguments").
from tpy import Own, Ptr, ValueType, int32, readonly


class ValSub(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Sub:
    vsub: ValSub

    def __init__(self) -> None:
        self.vsub = ValSub(3)


class Holder:
    _s: Sub

    def __init__(self) -> None:
        self._s = Sub()

    @property
    def ch(self) -> Sub:
        return self._s


def mk() -> Own[Holder]:
    return Holder()


def main() -> None:
    a = mk().ch.vsub.x
    # The pointer outlives the temporary it points into.
    p: Ptr[readonly[ValSub]] = mk().ch.vsub  # tpyc: error(/local_decl\.lends_from_temporary/)
    print(a, p.x)


main()
