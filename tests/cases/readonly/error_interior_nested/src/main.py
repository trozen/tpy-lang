# interior[interior[...]] is redundant -- the marker wraps a Ptr[T] once.
from tpy import Int32, Ptr, nocopy, interior


@nocopy
class Bad:
    p: interior[interior[Ptr[Int32]]]  # tpyc: error(/redundant/)

    def __init__(self, p: Ptr[Int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
