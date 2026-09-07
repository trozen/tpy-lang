# A plain @native member called on a POINTER-returning call receiver: the
# member reaches through the pointer and needs a deref check, which no
# call-receiver render spells.
from tpy import Int32, Ptr, nocopy, take_ptr
from tpy.extern import native


@native("tpy::MovableMutex")
@nocopy
class RawMu:
    def __init__(self) -> None: ...

    def lock(self) -> None: ...


class H:
    m: RawMu

    def __init__(self) -> None:
        self.m = RawMu()

    def ptr(self) -> Ptr[RawMu]:
        return take_ptr(self.m)

    def go(self) -> None:
        # The receiver is a call returning a pointer.
        self.ptr().lock()  # tpyc: error(/expr\.method_call/)


def main() -> None:
    H().go()
    print(1)


main()
