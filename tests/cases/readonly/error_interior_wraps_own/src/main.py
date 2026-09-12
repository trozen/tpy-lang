# unsafe_interior_mutable[...] wraps a plain Ptr[T] field; it cannot wrap Own[...].
from tpy import int32, Ptr, Own, nocopy, unsafe_interior_mutable


@nocopy
class Bad:
    p: unsafe_interior_mutable[Own[Ptr[int32]]]  # tpyc: error(/cannot wrap/)

    def __init__(self, p: Ptr[int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
