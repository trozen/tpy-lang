# Final[float32] with arithmetic between Finals and literals.
# Mirrors final_float_arith to exercise the constexpr path of
# truediv_f32 / floordiv_f32 / fmod_f32.
from typing import Final
from tpy import float32

A: Final[float32] = float32(1.5)
B: Final[float32] = float32(2.5)
SUM: Final[float32] = A + B
DIFF: Final[float32] = A - B
PROD: Final[float32] = A * B
QUOT: Final[float32] = A / B
FLR: Final[float32] = A // B
MODR: Final[float32] = A % B

def main() -> None:
    print(SUM, DIFF, PROD)
    print(QUOT, FLR, MODR)

main()
