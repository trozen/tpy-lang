# float32 mixed with every fixed-width int stays float32 (binop + augmented).
# no_cpython: single-precision formatting diverges from CPython's double float.
from tpy import float32, int8, int16, int64, uint8, uint16, uint32, uint64


def forward() -> None:
    a = float32(10.0) + int8(5)  # tpyc: type(float32)
    b = float32(10.0) - int16(3)  # tpyc: type(float32)
    c = float32(4.0) * int64(3)  # tpyc: type(float32)
    d = float32(12.0) / uint8(4)  # tpyc: type(float32)
    e = float32(13.0) // uint16(5)  # tpyc: type(float32)
    f = float32(13.0) % uint32(5)  # tpyc: type(float32)
    g = float32(2.0) ** uint64(3)  # tpyc: type(float32)
    print(a, b, c, d, e, f, g)


def reverse() -> None:
    a = int8(5) + float32(10.0)  # tpyc: type(float32)
    b = int16(3) - float32(10.0)  # tpyc: type(float32)
    c = int64(3) * float32(4.0)  # tpyc: type(float32)
    d = uint8(12) / float32(4.0)  # tpyc: type(float32)
    e = uint16(13) // float32(5.0)  # tpyc: type(float32)
    f = uint32(13) % float32(5.0)  # tpyc: type(float32)
    g = uint64(2) ** float32(3.0)  # tpyc: type(float32)
    print(a, b, c, d, e, f, g)


def augmented() -> None:
    y = float32(20.0)
    y += int8(5)
    y -= int16(5)
    y *= uint32(2)
    print(y)


def main() -> None:
    forward()
    reverse()
    augmented()


main()
