# A module-level reference-typed global as a METHOD RECEIVER. A global reads
# through its slot pointer, so the mutating and the reading calls both act on
# the one module-level object. Two legs of one shape: the bytearray receiver
# takes the same container receiver row the list global does.
from tpy import int32, uint8

BUF: bytearray = bytearray(b"ab")
NUMS: list[int32] = [1, 2]


def add(n: uint8) -> None:
    BUF.append(n)  # tpyc: ok


def add_num(n: int32) -> None:
    NUMS.append(n)


def main() -> None:
    add(67)
    print(len(BUF), BUF[2])
    print(BUF.upper())
    add_num(3)
    print(len(NUMS))


main()
