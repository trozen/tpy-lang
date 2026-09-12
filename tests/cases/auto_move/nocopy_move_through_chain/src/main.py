# Move-through chain: h -> a -> b, return b.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def chain() -> Own[Handle]:
    h = Handle(99)
    a = h
    b = a
    return b


def main():
    r = chain()
    print(r.fd)


main()
