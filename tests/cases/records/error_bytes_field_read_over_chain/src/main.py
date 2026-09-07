# A `bytes` FIELD read reached over a record field CHAIN (`h.g.b`): the bytes
# value-read row admits a bare receiver, not a chained one. TPy rejects
# printing `h.g.b` today.
from tpy import Own


class Inner:
    b: bytes

    def __init__(self, b: Own[bytes]) -> None:
        self.b = b


class Outer:
    g: Inner

    def __init__(self, g: Own[Inner]) -> None:
        self.g = g


def main() -> None:
    h = Outer(Inner(b"x"))
    print(h.g.b)  # tpyc: error(/field.result_type/)


main()
