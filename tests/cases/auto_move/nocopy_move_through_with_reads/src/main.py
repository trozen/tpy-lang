# Move-through with reads of alias between declaration and return.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def with_reads() -> Own[Handle]:
    h = Handle(7)
    alias = h
    print(alias.fd)
    return alias


def main():
    r = with_reads()
    print(r.fd)


main()
