# Move-through: alias=h at last use of h moves into alias, enabling return.
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


def transfer() -> Own[Handle]:
    h = Handle(10)
    alias = h
    return alias


def main():
    r = transfer()
    print(r.fd)


main()
