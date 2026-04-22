# Storing a reference-type param into a Ptr[T] field takes the address
# (_a(&a)), no copy happens -- the "copies X into field" warning must not fire.
# Field is Ptr[readonly[T]] because mutable Ptr[T] from a const-ref param
# currently emits non-compiling C++ (const T* -> T*); orthogonal to this test.
from tpy import Int32, Ptr, readonly

class A:
    v: Int32
    def __init__(self, v: Int32) -> None:
        self.v = v

class Holder:
    _a: Ptr[readonly[A]]
    def __init__(self, a: A) -> None:
        self._a = a  # tpyc: ok

def main() -> None:
    a = A(42)
    h = Holder(a)
    print(h._a.v)

main()
