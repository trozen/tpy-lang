# copy() on @nocopy type is a compile error
from tpy import int32, Own, nocopy, copy


@nocopy
class Handle:
    fd: int32


def main():
    h = Handle()
    h2 = copy(h)  # tpyc: error(/Cannot copy @nocopy/)


main()
