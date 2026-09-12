# @nocopy not-at-last-use error for generic constructor with Own[T] param
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


class GenericHolder[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item


def main():
    h = Handle()
    h.fd = 1
    gh = GenericHolder[Handle](h)  # tpyc: error(/@nocopy.*used after this point/)
    print(h.fd)


main()
