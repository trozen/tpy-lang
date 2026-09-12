# @nocopy not-at-last-use error for record method with Own[T] param
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


class Holder:
    h: Handle

    def take(self, h: Own[Handle]) -> None:
        self.h = h


def main():
    holder = Holder()
    holder.h = Handle()
    h = Handle()
    h.fd = 1
    holder.take(h)  # tpyc: error(/@nocopy.*used after this point/)
    print(h.fd)


main()
