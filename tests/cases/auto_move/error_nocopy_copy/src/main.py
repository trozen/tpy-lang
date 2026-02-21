# copy() on @nocopy type is a compile error
from tpy import Int32, Own, nocopy, copy


@nocopy
class Handle:
    fd: Int32


def main():
    h = Handle()
    h2 = copy(h)  # tpyc: error(/Cannot copy @nocopy/)


main()
