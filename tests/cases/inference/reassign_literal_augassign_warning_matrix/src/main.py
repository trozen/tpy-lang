# Augmented assignment type promotion matrix: reassigning with different
# int types (int32, BigInt, float) and the resulting widening/warnings.
from tpy import int32


def ret_i32() -> int32:
    return int32(7)


def main() -> None:
    x = 0
    x += int32(5)  # tpyc: ok
    print(x)

    y = 0
    y += ret_i32()  # tpyc: ok
    print(y)

    z: int = 0
    z += int32(5)  # tpyc: ok
    print(z)

    w = int(0)
    w += int32(5)  # tpyc: ok
    print(w)


main()
