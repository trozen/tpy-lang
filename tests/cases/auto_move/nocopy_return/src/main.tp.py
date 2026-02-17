# Return @nocopy at last use -- NRVO/implicit move
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32


def make(val: Int32) -> Own[Handle]:
    h = Handle()
    h.fd = val
    return h


def main():
    h = make(99)
    print(h.fd)


main()
