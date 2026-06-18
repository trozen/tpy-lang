# Float32 mixed with every fixed-width int stays Float32 (binop + augmented).
# no_cpython: single-precision formatting diverges from CPython's double float.
from tpy import Float32, Int8, Int16, Int64, UInt8, UInt16, UInt32, UInt64


def forward() -> None:
    a = Float32(10.0) + Int8(5)  # tpyc: type(Float32)
    b = Float32(10.0) - Int16(3)  # tpyc: type(Float32)
    c = Float32(4.0) * Int64(3)  # tpyc: type(Float32)
    d = Float32(12.0) / UInt8(4)  # tpyc: type(Float32)
    e = Float32(13.0) // UInt16(5)  # tpyc: type(Float32)
    f = Float32(13.0) % UInt32(5)  # tpyc: type(Float32)
    g = Float32(2.0) ** UInt64(3)  # tpyc: type(Float32)
    print(a, b, c, d, e, f, g)


def reverse() -> None:
    a = Int8(5) + Float32(10.0)  # tpyc: type(Float32)
    b = Int16(3) - Float32(10.0)  # tpyc: type(Float32)
    c = Int64(3) * Float32(4.0)  # tpyc: type(Float32)
    d = UInt8(12) / Float32(4.0)  # tpyc: type(Float32)
    e = UInt16(13) // Float32(5.0)  # tpyc: type(Float32)
    f = UInt32(13) % Float32(5.0)  # tpyc: type(Float32)
    g = UInt64(2) ** Float32(3.0)  # tpyc: type(Float32)
    print(a, b, c, d, e, f, g)


def augmented() -> None:
    y = Float32(20.0)
    y += Int8(5)
    y -= Int16(5)
    y *= UInt32(2)
    print(y)


def main() -> None:
    forward()
    reverse()
    augmented()


main()
