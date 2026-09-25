# A class name at a Callable slot whose result type its instance does not fit
# is rejected (CPython runs it), with a message naming the class.
from typing import Callable
from tpy import int32


class Pt:
    x: int32

    def __init__(self) -> None:
        self.x = 0


def main() -> None:
    f: Callable[[], int32] = Pt  # tpyc: error(/'Pt' used as a 'Callable\[\[\], int32\]' constructs a 'Pt', which is not compatible with its result type 'int32'/)
    print(f())


main()
