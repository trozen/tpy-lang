# A subclass of a generic-base instantiation inherits the base's
# argument-taking __init__, with the base's type params substituted.
from tpy import ValueType, int32


class Base[N: int](ValueType):
    _v: int32

    def __init__(self, x: int32 = 0) -> None:
        self._v = x


class Sub(Base[14]): ...          # tpyc: ok


class SubSub(Sub): ...


class Typed[T]:
    val: T

    def __init__(self, x: T) -> None:
        self.val = x  # tpyc: warning(/may copy/)


class TypedI(Typed[int32]): ...


# Generic subclass whose own param feeds the base instantiation.
class TypedM[M](Typed[M]): ...


def main() -> None:
    print(Sub(5)._v)
    print(Sub()._v)
    print(SubSub(9)._v)
    print(TypedI(42).val)
    print(TypedM[int32](7).val)


main()
