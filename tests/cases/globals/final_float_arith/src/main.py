# Final[float] with arithmetic between Finals and literals.
# Float helpers (truediv/floordiv/fmod) are constexpr, so compile-time
# div-by-zero becomes a C++ compile error (same as the int path).
from typing import Final

A: Final[float] = 1.5
B: Final[float] = 2.5
SUM: Final[float] = A + B
DIFF: Final[float] = A - B
PROD: Final[float] = A * B
QUOT: Final[float] = A / B
FLR: Final[float] = A // B
MODR: Final[float] = A % B

def main() -> None:
    print(SUM, DIFF, PROD)
    print(QUOT, FLR, MODR)

main()
