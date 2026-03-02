# True division (/) on fixed-width integer types returns float
from tpy import Int32, Int64, UInt32

def main() -> None:
    a: Int32 = Int32(10)
    b: Int32 = Int32(3)
    r1: float = a / b  # tpyc: type(float)
    print(r1)

    c: Int64 = Int64(100)
    d: Int64 = Int64(7)
    print(c / d)

    e: UInt32 = UInt32(15)
    f: UInt32 = UInt32(4)
    print(e / f)

    # Exact division
    g: Int32 = Int32(10)
    h: Int32 = Int32(5)
    print(g / h)

main()
