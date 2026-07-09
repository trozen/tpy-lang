# Error: unsafe_interior_mutable[...] wraps a plain Ptr[T] field; it cannot wrap
# readonly[...] (the two modifiers describe opposite ends of the readonly
# boundary).
from tpy import Int32, Ptr, nocopy, unsafe_interior_mutable, readonly


@nocopy
class Bad:
    p: unsafe_interior_mutable[readonly[Ptr[Int32]]]  # tpyc: error(/cannot wrap/)

    def __init__(self, p: Ptr[Int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
