# Final[IntN] with arithmetic/bitwise ops between Finals and literals
from typing import Final
from tpy import Int32, Int64

A: Final[Int32] = 5
B: Final[Int32] = 3
SUM: Final[Int32] = A + B
DIFF: Final[Int32] = A - B
PROD: Final[Int32] = A * B
QUOT: Final[Int32] = A // B
MODR: Final[Int32] = A % B
SHL: Final[Int32] = A << 2
SHR: Final[Int32] = A >> 1
POW: Final[Int32] = A ** 2
NESTED: Final[Int32] = (A + B) * 2 - 1
AND_: Final[Int32] = A & B
OR_: Final[Int32] = A | B
XOR: Final[Int32] = A ^ B

# Works on Int64 too
X: Final[Int64] = 100
Y: Final[Int64] = 7
Z: Final[Int64] = X * Y + 3

def main() -> None:
    print(SUM, DIFF, PROD, QUOT, MODR)
    print(SHL, SHR, POW, NESTED, AND_, OR_, XOR)
    print(Z)

main()
