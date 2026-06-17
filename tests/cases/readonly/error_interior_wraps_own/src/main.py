# interior[...] wraps a plain Ptr[T] field; it cannot wrap Own[...].
from tpy import Int32, Ptr, Own, nocopy, interior


@nocopy
class Bad:
    p: interior[Own[Ptr[Int32]]]  # tpyc: error(/cannot wrap/)

    def __init__(self, p: Ptr[Int32]) -> None:
        self.p = p


def main() -> None:
    pass


main()
