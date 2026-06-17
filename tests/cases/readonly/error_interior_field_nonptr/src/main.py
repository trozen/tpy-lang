# Error: interior[...] is a field-only escape hatch supported only on Ptr[T]
# (refcount-style hidden bookkeeping), not on a value field.
from tpy import Int32, nocopy, interior


@nocopy
class Bad:
    x: interior[Int32]  # tpyc: error(/only supported on a Ptr/)

    def __init__(self, x: Int32) -> None:
        self.x = x


def main() -> None:
    pass


main()
