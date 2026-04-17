# Final[Float32] with arithmetic between Finals and literals.
# Mirrors final_float_arith to exercise the constexpr path of
# truediv_f32 / floordiv_f32 / fmod_f32.
from typing import Final
from tpy import Float32

A: Final[Float32] = Float32(1.5)
B: Final[Float32] = Float32(2.5)
SUM: Final[Float32] = A + B
DIFF: Final[Float32] = A - B
PROD: Final[Float32] = A * B
QUOT: Final[Float32] = A / B
FLR: Final[Float32] = A // B
MODR: Final[Float32] = A % B

def main() -> None:
    print(SUM, DIFF, PROD)
    print(QUOT, FLR, MODR)

main()
