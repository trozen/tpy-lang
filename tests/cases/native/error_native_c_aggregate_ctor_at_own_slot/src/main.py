# A C-binding @native record constructed at an owning slot: a C binding
# builds through aggregate initialization, a render the record argument
# branch does not spell.
from tpy import int32, Ptr
from tpy.extern import native
from tpy.unsafe import unsafe_take


@native("tpy::MovableConditionVariable", binding="C")
class RawC:
    def __init__(self) -> None: ...


class H:
    q: Ptr[RawC]

    def __init__(self) -> None:
        # The constructor call is the owning argument.
        self.q = unsafe_take(RawC())  # tpyc: error(/expr\.call:call\.native_arg\.own/)


def main() -> None:
    h = H()
    print(1)


main()
