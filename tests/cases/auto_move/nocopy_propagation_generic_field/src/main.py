# Class with list[NocopyType] field is implicitly nocopy
from tpy import Int32, Own, nocopy


@nocopy
class Handle:
    fd: Int32

    def __init__(self, fd: Int32):
        self.fd = fd


class Handles:
    items: list[Handle]

    def __init__(self):
        self.items = []


def consume(h: Own[Handles]) -> None:
    pass


def main():
    h = Handles()
    consume(h)  # tpyc: ok
    print("ok")


main()
