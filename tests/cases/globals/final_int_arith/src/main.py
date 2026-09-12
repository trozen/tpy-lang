# Final[IntN] with arithmetic/bitwise ops between Finals and literals
from typing import Final
from tpy import int32, int64

A: Final[int32] = 5
B: Final[int32] = 3
SUM: Final[int32] = A + B
DIFF: Final[int32] = A - B
PROD: Final[int32] = A * B
QUOT: Final[int32] = A // B
MODR: Final[int32] = A % B
SHL: Final[int32] = A << 2
SHR: Final[int32] = A >> 1
POW: Final[int32] = A ** 2
NESTED: Final[int32] = (A + B) * 2 - 1
AND_: Final[int32] = A & B
OR_: Final[int32] = A | B
XOR: Final[int32] = A ^ B

# Works on int64 too
X: Final[int64] = 100
Y: Final[int64] = 7
Z: Final[int64] = X * Y + 3

def main() -> None:
    print(SUM, DIFF, PROD, QUOT, MODR)
    print(SHL, SHR, POW, NESTED, AND_, OR_, XOR)
    print(Z)

main()
