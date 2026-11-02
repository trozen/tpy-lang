# A store to a getter-only property through a Ptr is rejected as on a
# plain receiver: there is no setter to call.
from tpy import int32, Ptr


class Box:
    _v: int32

    def __init__(self) -> None:
        self._v = 0

    @property
    def v(self) -> int32:
        return self._v


def write(p: Ptr[Box]) -> None:
    # the store through the Ptr finds no setter
    p.v = 1  # tpyc: error(/Property 'v' is read-only \(no setter defined\)/)


def main() -> None:
    b = Box()
    write(b)


main()
