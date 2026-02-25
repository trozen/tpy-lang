# No move-through: h is used after alias declaration, so not last use.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


def bad() -> Own[Handle]:
    h = Handle(5)
    alias = h
    print(h.fd)
    return alias  # tpyc: error(/@nocopy.*cannot be returned/)


def main():
    bad()


main()
