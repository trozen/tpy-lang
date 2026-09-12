# Passing implicitly-nocopy type to Own[T] not at last use gives error
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


class Container:
    handle: Handle

    def __init__(self, handle: Own[Handle]):
        self.handle = handle


def consume(c: Own[Container]) -> int32:
    return c.handle.fd


def main():
    c = Container(Handle(42))
    consume(c)  # tpyc: error(/non-copyable type.*is used after this point/)
    print(c.handle.fd)


main()
