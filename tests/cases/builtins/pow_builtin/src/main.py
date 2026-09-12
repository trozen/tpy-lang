# Test pow() builtin for int32, BigInt, and float
from tpy import int32

def main() -> None:
    # int32 pow
    a: int32 = pow(int32(2), int32(10))
    print(a)
    print(pow(int32(3), int32(0)))
    print(pow(int32(-2), int32(3)))

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
