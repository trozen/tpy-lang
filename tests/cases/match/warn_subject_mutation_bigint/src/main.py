# A BigInt (`int`) binding is NOT a free-copy scalar (heap-backed arbitrary
# precision), so it stays an auto& borrow and warns on subject mutation --
# unlike a fixed-width Int32, which would be copied and safe.
from tpy import Int32


class Big:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


class Small:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Holder:
    item: Big | Small

    def __init__(self) -> None:
        self.item = Small(3)


def poke(h: Holder) -> None:
    match h.item:
        case Big(n=b):
            h.item = Small(9)  # tpyc: warning(/'h.item' is mutated in this arm while pattern bindings borrow/)
            print(b)
        case Small(v=v):
            print("small", v)


def main() -> None:
    poke(Holder())


main()
