# unsafe_interior_mutable[unsafe_interior_mutable[...]] is redundant -- the marker wraps a
# Ptr[T] once.
from tpy import Int32, Ptr, nocopy, unsafe_interior_mutable


@nocopy
class Bad:
    p: unsafe_interior_mutable[unsafe_interior_mutable[Ptr[Int32]]]  # tpyc: error(/redundant/)

    def __init__(self, p: Ptr[Int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
