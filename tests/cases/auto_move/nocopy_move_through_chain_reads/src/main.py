# Move-through chain with reads between links: h -> a (read) -> b -> return b.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def chain_reads() -> Own[Handle]:
    h = Handle(33)
    a = h
    print(a.fd)
    b = a
    return b


def main():
    r = chain_reads()
    print(r.fd)


main()
