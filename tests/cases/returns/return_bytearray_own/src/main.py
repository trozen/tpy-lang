# A bytearray local returned at an Own[bytearray] slot renders as the plain
# name -- `return buf;` -- the way a list/dict/set local already does at that
# slot: the slot takes the vector by value and the return moves it, so no
# std::move, copy or pointer lift is spelled.
from tpy import Int32, Own, UInt8


def make(n: Int32) -> Own[bytearray]:
    buf = bytearray(n)
    buf[0] = 7
    return buf  # tpyc: ok


class Canvas:
    fill: UInt8

    def __init__(self, fill: UInt8) -> None:
        self.fill = fill

    def draw(self, n: Int32) -> Own[bytearray]:
        # The method slot moves the local out exactly as the free function's.
        out = bytearray(n)
        out[1] = self.fill
        return out  # tpyc: ok


def main() -> None:
    b = make(4)
    # The moved-out buffer is the caller's to mutate.
    b[1] = 9
    for v in b:
        print(v)
    d = Canvas(5).draw(3)
    for v in d:
        print(v)


main()
