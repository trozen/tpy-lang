# copy() on implicitly-nocopy type gives clear error explaining why
from tpy import Int32, Own, nocopy, copy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


class Container:
    handle: Handle

    def __init__(self, handle: Own[Handle]):
        self.handle = handle


def main():
    c = Container(Handle(1))
    c2 = copy(c)  # tpyc: error(/Cannot copy non-copyable type/)


main()
