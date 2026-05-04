# `p = (a, b)` where elements are @nocopy locals would produce a ref-tuple
# (tuple<Handle&, Handle&>) under the default inference. Such a tuple cannot
# be converted to a value tuple later (the conversion target of any return /
# Own[Tuple] slot), and the C++ error is cryptic. Sema rejects up-front.
from tpy import nocopy, Int32


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32) -> None:
        self.fd = fd


def main() -> None:
    a = Handle(Int32(1))
    b = Handle(Int32(2))
    p = (a, b)  # tpyc: error(/cannot bind tuple element 0 of non-copyable type/)
    print(p[0].fd)


main()
