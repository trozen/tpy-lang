# A ValueType field holding a class instance inside a tuple is refused, as a
# bare class field is (LANGUAGE_FEATURES ValueType "Validation rules").
from tpy import int32, ValueType


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class V(ValueType):
    t: tuple[A, int32]  # tpyc: error(/field 't' has type 'tuple\[A, int32\]', which holds a reference type/)

    def __init__(self, t: tuple[A, int32]) -> None:
        self.t = t


def main() -> None:
    v = V((A(1), 2))
    w = v
    v.t[0].v = 9
    print(v.t[0].v, w.t[0].v)


main()
