# Return @nocopy at last use -- NRVO/implicit move
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


def make(val: int32) -> Own[Handle]:
    h = Handle()
    h.fd = val
    return h


def main():
    h = make(99)
    print(h.fd)


main()
