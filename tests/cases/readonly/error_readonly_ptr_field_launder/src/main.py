# A readonly Ptr FIELD only stops the slot being re-pointed; its pointee stays
# writable, so storing a Ptr[readonly[A]] into it would launder the readonly
# pointee into a writable one.
from tpy import Ptr, int32, readonly


class A:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class H:
    ro: readonly[Ptr[A]]

    def __init__(self, rp: Ptr[readonly[A]]) -> None:
        # the subject: the store would drop the pointee's readonly
        self.ro = rp  # tpyc: error(/expected Ptr\[A\], got Ptr\[readonly\[A\]\]/)

    def poke(self) -> None:
        self.ro.n = 5


def main() -> None:
    a = A()
    H(a).poke()
    print(a.n)


main()
