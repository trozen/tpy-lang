# Storing a reference-type param into a mutable Ptr[T] field takes the param's
# address. tpyc's default `const T&` perf emission for non-mutated ref-params
# must be suppressed in this case -- `&a` on a `const T&` yields `const T*`,
# which won't implicitly convert to the field's `T*`.
from tpy import Ptr, int32, readonly

class A:
    v: int32
    def __init__(self, v: int32) -> None:
        self.v = v

class Mut:
    _a: Ptr[A]
    # Param escapes via mutable Ptr[A] field -> must emit `A& a`, not `const A& a`.
    def __init__(self, a: A) -> None:
        self._a = a
    # Same escape through a non-__init__ method.
    def set_a(self, a: A) -> None:
        self._a = a

class Const:
    _a: Ptr[readonly[A]]
    # Ptr[readonly[A]] field is `const A*`; matching `const A& a` stays const.
    def __init__(self, a: A) -> None:
        self._a = a

def main() -> None:
    a = A(42)
    m = Mut(a)
    print(m._a.v)
    b = A(99)
    m.set_a(b)
    print(m._a.v)
    c = Const(a)
    print(c._a.v)

main()
