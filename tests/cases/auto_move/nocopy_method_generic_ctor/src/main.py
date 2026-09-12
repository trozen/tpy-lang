# @nocopy auto-moved through record method and generic ctor at last use.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32


class Holder:
    h: Handle

    def take(self, h: Own[Handle]) -> None:
        self.h = h


class GenericHolder[T]:
    item: T

    def __init__(self, item: Own[T]):
        self.item = item

    def replace(self, item: Own[T]) -> None:
        self.item = item


def main():
    # @nocopy via record method at last use
    holder = Holder()
    holder.h = Handle()
    h1 = Handle()
    h1.fd = 10
    holder.take(h1)  # tpyc: ok
    print(holder.h.fd)

    # @nocopy via generic ctor at last use
    h2 = Handle()
    h2.fd = 20
    gh = GenericHolder[Handle](h2)  # tpyc: ok
    print(gh.item.fd)

    # @nocopy via generic method at last use
    h3 = Handle()
    h3.fd = 30
    gh.replace(h3)  # tpyc: ok
    print(gh.item.fd)


main()
