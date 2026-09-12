# float (float64) mixes with every fixed-width int width (incl. int32), both
# operand orders, all 7 arithmetic ops. Whole-number results for CPython parity.
from tpy import int8, int16, int32, int64, uint8, uint16, uint32, uint64


def forward() -> None:
    print(10.0 + int8(5))
    print(10.0 - int16(3))
    print(4.0 * int32(3))
    print(12.0 / int64(4))
    print(13.0 // uint8(5))
    print(13.0 % uint16(5))
    print(2.0 ** uint32(3))
    print(10.0 + uint64(5))


def reverse() -> None:
    print(int8(5) + 10.0)
    print(int16(3) - 10.0)
    print(int32(3) * 4.0)
    print(int64(12) / 4.0)
    print(uint8(13) // 5.0)
    print(uint16(13) % 5.0)
    print(uint32(2) ** 3.0)
    print(uint64(5) + 10.0)


def main() -> None:
    forward()
    reverse()


main()
