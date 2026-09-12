# Class with list[NocopyType] field is implicitly nocopy
from tpy import int32, Own, nocopy


@nocopy
class Handle:
    fd: int32

    def __init__(self, fd: int32):
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
