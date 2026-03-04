# Test pow() builtin for Int32, BigInt, and float
from tpy import Int32

def main() -> None:
    # Int32 pow
    a: Int32 = pow(Int32(2), Int32(10))
    print(a)
    print(pow(Int32(3), Int32(0)))
    print(pow(Int32(-2), Int32(3)))

    # BigInt pow
    b = pow(int(2), int(30))
    print(b)
    c = pow(int(10), int(3))
    print(c)

    # float pow
    d: float = pow(2.0, 0.5)
    print(d)
    print(pow(3.0, 2.0))

main()
