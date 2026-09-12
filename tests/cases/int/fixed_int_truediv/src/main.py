# True division (/) on fixed-width integer types returns float
from tpy import int32, int64, uint32

def main() -> None:
    a: int32 = int32(10)
    b: int32 = int32(3)
    r1: float = a / b  # tpyc: type(float)
    print(r1)

    c: int64 = int64(100)
    d: int64 = int64(7)
    print(c / d)

    e: uint32 = uint32(15)
    f: uint32 = uint32(4)
    print(e / f)

    # Exact division
    g: int32 = int32(10)
    h: int32 = int32(5)
    print(g / h)

main()
