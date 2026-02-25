# Class containing a @nocopy field becomes implicitly nocopy, can be moved at last use
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


class Container:
    handle: Handle

    def __init__(self, handle: Own[Handle]):
        self.handle = handle


def consume(c: Own[Container]) -> Int32:
    return c.handle.fd


def main():
    c = Container(Handle(42))
    print(consume(c))  # tpyc: ok


main()
