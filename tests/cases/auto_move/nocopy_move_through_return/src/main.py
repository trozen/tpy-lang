# Move-through: alias=h at last use of h moves into alias, enabling return.
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
        self.fd = fd


def transfer() -> Own[Handle]:
    h = Handle(10)
    alias = h
    return alias


def main():
    r = transfer()
    print(r.fd)


main()
