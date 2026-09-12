# Test divmod() builtin for int32, BigInt, and float
from tpy import int32

def main() -> None:
    # int32 divmod
    q1, r1 = divmod(int32(17), int32(5))
    print(q1)
    print(r1)

    # int32 divmod with negative (Python floor semantics)
    q2, r2 = divmod(int32(-17), int32(5))
    print(q2)
    print(r2)

    # BigInt divmod
    q3, r3 = divmod(int(100), int(7))
    print(q3)
    print(r3)

    # float divmod
    q4, r4 = divmod(7.5, 2.5)
    print(q4)
    print(r4)

    q5, r5 = divmod(10.0, 3.0)
    print(q5)
    print(r5)

main()
