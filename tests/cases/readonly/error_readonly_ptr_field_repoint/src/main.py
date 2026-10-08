# readonly[Ptr[A]] on a field makes the SLOT readonly: it cannot be re-pointed
# after construction, while what it points at stays writable.
from tpy import Ptr, int32, readonly


class A:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class H:
    ro: readonly[Ptr[A]]

    def __init__(self, a: A) -> None:
        self.ro = a

    def repoint(self, b: A) -> None:
        self.ro = b  # tpyc: error(/Cannot assign to readonly field 'ro'/)


def main() -> None:
    h = H(A())
    h.repoint(A())


main()
