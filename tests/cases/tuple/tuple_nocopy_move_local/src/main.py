# @nocopy locals at their LAST USE auto-MOVE into a tuple local (the local then
# owns them, storage form `std::tuple<Handle, Handle>`). The move is what makes
# this compile: a silent copy of a @nocopy type would be a compile error. The
# not-last-use form is rejected up-front (see error_tuple_nocopy_ref_inference).
from tpy import nocopy, int32


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32) -> None:
        self.fd = fd


def main() -> None:
    a = Handle(int32(1))
    b = Handle(int32(2))
    p = (a, b)  # tpyc: ok
    print(p[0].fd)
    print(p[1].fd)


main()
