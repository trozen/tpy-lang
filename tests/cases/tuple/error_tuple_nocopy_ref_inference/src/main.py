# `p = (a, b)` where @nocopy locals are NOT at last use binds them by reference
# (tuple<Handle&, Handle&>) under the default inference. Such a ref-tuple has no
# path to a value tuple later (the conversion target of any return / Own[Tuple]
# slot), and the C++ error is cryptic, so sema rejects up-front. (At last use
# the locals auto-MOVE into a value tuple instead -- see tuple_nocopy_move_local.)
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
    print(a.fd)
    print(b.fd)


main()
