# unsafe_interior_mutable[unsafe_interior_mutable[...]] is redundant -- the marker wraps a
# Ptr[T] once.
from tpy import int32, Ptr, nocopy, unsafe_interior_mutable


@nocopy
class Bad:
    p: unsafe_interior_mutable[unsafe_interior_mutable[Ptr[int32]]]  # tpyc: error(/redundant/)

    def __init__(self, p: Ptr[int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
