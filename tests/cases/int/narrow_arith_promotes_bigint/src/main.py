# Under --default-int=BigInt an 8/16-bit operator computes in int, exactly;
# a Final computed through one is a runtime constant, and so is one reading it.
from typing import Final
from tpy import int8, int16, int32, uint16

G8: Final[int8] = 100
G16: Final[int16] = G8 + 1  # tpyc: ok
G32: Final[int32] = G16  # tpyc: ok


class Consts:
    X: Final[int8] = G8 + 1  # tpyc: ok
    Y: Final[int16] = G16  # tpyc: ok


def main() -> None:
    a8 = int8(100)
    u16 = uint16(60000)
    s = a8 + a8  # tpyc: type(int)
    p = u16 * u16 * u16 * u16  # tpyc: type(int)
    sh = a8 << 70  # tpyc: type(int)
    x8: int8 = a8 - 50
    x8 += 1  # tpyc: ok
    print(s, p, sh, x8, G16, G32, Consts.X, Consts.Y)
    # pow() / divmod() compute in int too
    pw = pow(a8, int8(9))  # tpyc: type(int)
    q, r = divmod(int8(-7), int8(2))
    print(pw, q, r)


main()
