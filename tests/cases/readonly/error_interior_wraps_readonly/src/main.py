# Error: unsafe_interior_mutable[...] wraps a plain Ptr[T] field; it cannot wrap
# readonly[...] (the two modifiers describe opposite ends of the readonly
# boundary).
from tpy import int32, Ptr, nocopy, unsafe_interior_mutable, readonly


@nocopy
class Bad:
    p: unsafe_interior_mutable[readonly[Ptr[int32]]]  # tpyc: error(/cannot wrap/)

    def __init__(self, p: Ptr[int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
