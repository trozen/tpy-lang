# Final with primitive type-constructor calls as initializers.
from typing import Final
from tpy import Int32, Int64, UInt8, Float32, Char

SMALL: Final[Int32] = Int32(42)
BIG: Final[Int64] = Int64(SMALL)
BYTE: Final[UInt8] = UInt8(255)
HALF: Final[Float32] = Float32(0.5)
NEG: Final[Int32] = Int32(-1)
WIDE: Final[int] = int(SMALL)
DBL: Final[float] = float(SMALL)
FLAG: Final[bool] = bool(1)
CH: Final[Char] = Char(65)

def main() -> None:
    print(SMALL)
    print(BIG)
    print(BYTE)
    print(HALF)
    print(NEG)
    print(WIDE)
    print(DBL)
    print(FLAG)
    print(CH)

main()
