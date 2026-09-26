from tpy import int32, int64


class A:
    cc: bytearray

    def __init__(self) -> None:
        self.cc = bytearray(b"xyz")

    def prefix(self) -> bytes:
        return self.cc[0:2]


def main() -> None:
    a = A()
    print(a.cc)


main()
