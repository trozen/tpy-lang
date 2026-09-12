# Final with primitive type-constructor calls as initializers.
from typing import Final
from tpy import int32, int64, uint8, float32, char

SMALL: Final[int32] = int32(42)
BIG: Final[int64] = int64(SMALL)
BYTE: Final[uint8] = uint8(255)
HALF: Final[float32] = float32(0.5)
NEG: Final[int32] = int32(-1)
WIDE: Final[int] = int(SMALL)
DBL: Final[float] = float(SMALL)
FLAG: Final[bool] = bool(1)
CH: Final[char] = char(65)

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
