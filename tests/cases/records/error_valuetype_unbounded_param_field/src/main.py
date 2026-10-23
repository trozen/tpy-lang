# A generic ValueType record storing an unbounded type parameter is refused
# (LANGUAGE_FEATURES ValueType "Validation rules": a type parameter stored in
# a field needs a ValueType bound): V[A] would copy the A that CPython aliases.
from tpy import int32, ValueType


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


class V[T](ValueType):
    def __init__(self, x: T) -> None:
        self.x = x  # tpyc: error(/ValueType class 'V': field 'x' has non-value type 'T'/)


def main() -> None:
    a = A(1)
    v = V[A](a)
    w = v
    v.x.v = 9
    print(v.x.v, w.x.v, a.v)


main()
